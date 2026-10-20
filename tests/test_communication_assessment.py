"""Exercise the complete communication verdict independently of legacy review."""

import json
import sys

import httpx
import httpx2
import pytest
from local_model_helpers import fixture_config, fixture_evidence
from pydantic import ValidationError
from rubric_model_helpers import RubricServer
from test_cli import ROOT, cli
from test_jev import hosted_response
from test_llm_decisions import LLMServer, choice, settings

from pr_review_router.assessment_engine import AssessmentReport, assess_pull_request
from pr_review_router.assessment_rubric import (
    AssessmentRubric,
    CriterionAssessment,
    CriterionBatch,
    load_assessment_rubric,
)
from pr_review_router.assessment_runtime import run_assessment
from pr_review_router.config import Policy
from pr_review_router.model_catalog import load_decider_settings
from pr_review_router.provider_config import JevSettings, MockSettings
from pr_review_router.transport import ReplayMismatch

pytestmark = pytest.mark.unit


def evidence():
    """Supply complete patches and a meaningful conventional title.

    :returns: Sanitized PR evidence with a populated Summary section.
    """
    return fixture_evidence().model_copy(
        update={
            "title": "docs: remove a repeated word",
            "body": "## Summary\nRemove the repeated word.\n",
        }
    )


def rubric():
    """Configure one formal gate, one semantic gate, and one advisory criterion.

    :returns: Explicit communication-only rubric.
    """
    return AssessmentRubric.model_validate(
        {
            "rubric_version": 1,
            "criteria": [
                {
                    "criterion_id": "title",
                    "description": "Conventional title",
                    "check": "conventional_title",
                    "action": "block",
                    "suggestion": "Use a conventional title.",
                },
                {
                    "criterion_id": "accuracy",
                    "description": "Accurate description",
                    "check": "semantic",
                    "instructions": "Compare the description with the patches.",
                    "action": "block",
                    "suggestion": "Describe the changes accurately.",
                },
                {
                    "criterion_id": "testing",
                    "description": "Relevant validation",
                    "check": "semantic",
                    "instructions": "Assess the validation explanation.",
                    "action": "suggest",
                    "suggestion": "Explain relevant validation.",
                },
            ],
        }
    )


def execute(statuses=None, **kwargs):
    """Run real local adapter code through an explicitly synthetic HTTP server.

    :param statuses: Scripted semantic statuses.
    :param kwargs: Additional assessment-runtime arguments.
    :returns: Assessment run and captured HTTP server.
    """
    server = RubricServer(statuses)
    result = run_assessment(
        evidence(),
        Policy(),
        rubric(),
        fixture_config().decision,
        http_transport=httpx.MockTransport(server.handle),
        **kwargs,
    )
    return result, server


@pytest.mark.parametrize(
    "statuses,verdict,suggestions",
    [
        ({}, "accepted", 0),
        ({"testing": "failed"}, "accepted", 1),
        ({"testing": "uncertain"}, "accepted", 0),
        ({"accuracy": "failed"}, "rejected", 0),
        ({"accuracy": "uncertain"}, "unresolved", 0),
        ({"accuracy": "failed", "testing": "uncertain"}, "rejected", 0),
    ],
)
def test_final_verdict_and_suggestions_without_any_review(statuses, verdict, suggestions):
    result, server = execute(statuses)
    report = result.report
    assert report.verdict == verdict
    assert len(report.suggestions) == suggestions
    assert report.automation_authorized is False
    assert report.report_type == "pr_communication"
    assert server.review_calls == 0
    assert server.decision_calls == 1
    assert all(request.url.port == 11435 for request in server.requests)
    payload = json.loads(next(r.content for r in server.requests if r.url.path == "/v1/systemone"))
    assert set(payload["state"]) == {"pr"}
    assert "review_text" not in payload["state"]
    assert "review" not in payload["questions"]
    semantic = next(c for c in report.criteria if c.criterion_id == "accuracy")
    assert semantic.confidence.source == "unavailable"
    assert semantic.native_answer["confidence"] == 0.985


def test_formal_only_requires_no_provider_and_retains_suggestions(tmp_path):
    formal = load_assessment_rubric(ROOT / "examples/pr-text/assessment.toml")
    run = run_assessment(
        evidence(),
        Policy(),
        formal,
        fixture_config().decision,
        http_transport=httpx.MockTransport(lambda _: pytest.fail("HTTP called")),
    )
    assert run.report.verdict == "accepted"
    assert run.artifact["http_calls"] == 0
    assert len(run.report.suggestions) == 1
    assert run.report.provider == "rubric:formal"


@pytest.mark.parametrize("failure", ["patch", "title", "budget"])
def test_preflight_and_formal_failures_never_request_models(failure):
    item = evidence()
    policy = Policy()
    if failure == "patch":
        item = item.model_copy(update={"files_complete": False})
    elif failure == "title":
        item = item.model_copy(update={"title": "not conventional"})
    else:
        policy = Policy(max_input_bytes=1)
    server = RubricServer()
    run = run_assessment(
        item,
        policy,
        rubric(),
        fixture_config().decision,
        http_transport=httpx.MockTransport(server.handle),
    )
    assert run.report.verdict == ("rejected" if failure == "title" else "unresolved")
    assert server.requests == []
    assert any(c.source == "unavailable" for c in run.report.criteria)


def test_offline_semantics_cannot_accept_editorial_changes_or_fixture_markers():
    report = assess_pull_request(evidence(), Policy(), rubric())
    assert report.verdict == "unresolved"
    assert next(c for c in report.criteria if c.criterion_id == "accuracy").source == "unavailable"


def test_report_schema_rejects_forged_acceptance():
    result, _ = execute({"accuracy": "failed"})
    raw = result.report.model_dump(mode="json")
    raw["verdict"] = "accepted"
    with pytest.raises(ValidationError):
        AssessmentReport.model_validate(raw)


def test_supplied_review_criterion_rejected_before_inference():
    raw = rubric().model_dump(mode="json")
    raw["criteria"][1]["target"] = "review"
    with pytest.raises(ValueError, match="supplied reviews"):
        run_assessment(evidence(), Policy(), AssessmentRubric.model_validate(raw))


def test_failed_provider_keeps_known_formal_results_and_hides_exceptions():
    class Broken:
        def assess(self, _):
            raise RuntimeError("synthetic-secret-never-export")

    report = assess_pull_request(evidence(), Policy(), rubric(), Broken())
    assert report.verdict == "unresolved"
    assert report.criteria[0].status == "passed"
    assert "synthetic-secret" not in report.model_dump_json()


@pytest.mark.parametrize(
    "failures,expected_calls,verdict", [(1, 2, "accepted"), (2, 2, "unresolved")]
)
def test_native_schema_retry_is_bounded(failures, expected_calls, verdict):
    server = RubricServer()
    attempts = []

    def handler(request):
        if request.url.path == "/v1/systemone":
            attempts.append(json.loads(request.content))
            if len(attempts) <= failures:
                return httpx.Response(200, json={"answers": {}})
        return server.handle(request)

    run = run_assessment(
        evidence(),
        Policy(),
        rubric(),
        fixture_config().decision,
        http_transport=httpx.MockTransport(handler),
    )
    assert run.report.verdict == verdict
    assert len(attempts) == expected_calls
    assert "exactly the requested" in attempts[-1]["questions"]["accuracy"]["instructions"]


def test_confidence_abstention_is_a_relevant_unknown():
    raw = rubric().model_dump(mode="json")
    raw["criteria"][1]["uncertain_below_confidence"] = 0.999
    server = RubricServer()
    run = run_assessment(
        evidence(),
        Policy(),
        AssessmentRubric.model_validate(raw),
        fixture_config().decision,
        http_transport=httpx.MockTransport(server.handle),
    )
    assert run.report.verdict == "unresolved"


def test_choice_routing_cannot_erase_confidence_abstention():
    raw = rubric().model_dump(mode="json")
    raw["criteria"][1]["uncertain_below_confidence"] = 0.999
    raw["routing_rules"] = [
        {"target_criterion_id": "accuracy", "when": {"accuracy": ["uncertain"]}, "status": "passed"}
    ]
    server = RubricServer()
    run = run_assessment(
        evidence(),
        Policy(),
        AssessmentRubric.model_validate(raw),
        fixture_config().decision,
        http_transport=httpx.MockTransport(server.handle),
    )
    assert run.report.verdict == "unresolved"


@pytest.mark.parametrize("kind", ["noul", "predicate", "score"])
def test_numeric_questions_preserve_normalization(kind):
    raw = rubric().model_dump(mode="json")
    raw["criteria"][1].update(question_type=kind, fail_threshold=0.2, pass_threshold=0.8)
    if kind == "score":
        raw["criteria"][1]["levels"] = ["incorrect", "unclear", "accurate"]
    server = RubricServer()
    run = run_assessment(
        evidence(),
        Policy(),
        AssessmentRubric.model_validate(raw),
        fixture_config().decision,
        http_transport=httpx.MockTransport(server.handle),
    )
    assert run.report.verdict == "accepted"


def test_recording_replays_exactly_and_rejects_legacy_tapes(tmp_path):
    run, _ = execute(record=True)
    tape = tmp_path / "assessment.json"
    tape.write_text(run.tape.model_dump_json())
    replay = run_assessment(
        evidence(),
        Policy(),
        rubric(),
        fixture_config().decision,
        replay=tape,
        http_transport=httpx.MockTransport(lambda _: pytest.fail("live fallback")),
    )
    assert replay.report == run.report
    legacy_tape = next((ROOT / "examples/local-models/recordings").glob("*.json"))
    with pytest.raises(ReplayMismatch):
        run_assessment(
            evidence(), Policy(), rubric(), fixture_config().decision, replay=legacy_tape
        )
    data = json.loads(tape.read_text())
    data["exchanges"].append(data["exchanges"][-1])
    tape.write_text(json.dumps(data))
    with pytest.raises(ReplayMismatch):
        run_assessment(evidence(), Policy(), rubric(), fixture_config().decision, replay=tape)


def test_general_llm_remains_a_decider_without_a_review():
    server = LLMServer(
        [
            {
                "answers": {
                    "accuracy": choice("passed", ["passed", "failed", "uncertain"]),
                    "testing": choice("passed", ["passed", "failed", "uncertain"]),
                }
            }
        ]
    )
    run = run_assessment(
        evidence(),
        Policy(),
        rubric(),
        settings(),
        http_transport=httpx.MockTransport(server.handle),
    )
    assert run.report.verdict == "accepted"
    assert server.review_calls == 0
    assert server.decision_calls == 1
    assert run.report.criteria[1].native_answer["source"] == "llm_self_reported"


def test_hosted_decider_and_replay_need_no_review_service(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-private-key")
    config = JevSettings(allow_network=True, allow_recording=True, max_retries=0)
    run = run_assessment(
        evidence(),
        Policy(),
        rubric(),
        config,
        record=True,
        hosted_transport=httpx2.MockTransport(hosted_response),
    )
    assert run.report.verdict == "accepted"
    path = tmp_path / "hosted.json"
    path.write_text(run.tape.model_dump_json())
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert run_assessment(evidence(), Policy(), rubric(), config, replay=path).report == run.report


class Static:
    def __init__(self, results, labels=None):
        self.batch = CriterionBatch(
            provider="synthetic:tests",
            labels=labels or {},
            criteria=[
                CriterionAssessment(
                    criterion_id=k, status=v, source="deterministic", explanation="Fixture"
                )
                for k, v in results.items()
            ],
        )

    def assess(self, _):
        return self.batch


def conditional_rubric():
    raw = rubric().model_dump(mode="json")
    for c in raw["criteria"]:
        c["action"] = None
    raw["criteria"].append(
        {
            "criterion_id": "scale",
            "description": "Communication requirements",
            "check": "semantic",
            "instructions": "Choose the scope.",
            "suggestion": "Clarify scope.",
            "choices": [
                {"label": "small", "description": "Small", "status": "passed"},
                {"label": "large", "description": "Large", "status": "passed"},
                {"label": "unknown", "description": "Unknown", "status": "uncertain"},
            ],
        }
    )
    raw["enforcement_rules"] = [
        {"when": {"scale": ["small"]}, "enforce": ["title", {"any_of": ["accuracy", "testing"]}]},
        {
            "when": {"scale": ["large"]},
            "enforce": ["title", "accuracy"],
            "soft_enforce": ["testing"],
        },
    ]
    return AssessmentRubric.model_validate(raw)


@pytest.mark.parametrize(
    "label,accuracy,testing,verdict,selected",
    [
        ("small", "uncertain", "passed", "accepted", 0),
        ("large", "uncertain", "passed", "unresolved", 1),
        ("small", "failed", "failed", "rejected", 0),
        ("unknown", "failed", "failed", "unresolved", None),
    ],
)
def test_rules_any_of_and_unknown_applicability(label, accuracy, testing, verdict, selected):
    provider = Static(
        {
            "title": "passed",
            "accuracy": accuracy,
            "testing": testing,
            "scale": "uncertain" if label == "unknown" else "passed",
        },
        {"scale": label},
    )
    report = assess_pull_request(evidence(), Policy(), conditional_rubric(), provider)
    assert report.verdict == verdict
    assert report.selected_enforcement_rule == selected
    if label == "unknown":
        assert report.blockers == []
        assert report.unresolved == ["rules:selection"]


def test_decider_only_catalog_ignores_incompatible_reviewer(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[defaults]\ndecider="only"\nreviewer="missing"\n'
        '[models.only]\nbackend="mock"\n[models.bad-reviewer]\nbackend="not-supported"\n'
    )
    assert load_decider_settings(path) == MockSettings()
    path.write_text(path.read_text().replace('reviewer="missing"', "reviewer=42"))
    assert load_decider_settings(path) == MockSettings()
    with pytest.raises(ValueError):
        load_decider_settings(path, overrides=["review.temperature=0.5"])


@pytest.mark.integration
@pytest.mark.parametrize("name,verdict", [("valid", "accepted"), ("invalid", "rejected")])
def test_cli_assessment_emits_final_json_without_reviewer(tmp_path, name, verdict):
    report = tmp_path / "report.json"
    completed = cli(
        "assess",
        "--input",
        ROOT / f"examples/pr-text/{name}.json",
        "--assessment-rubric",
        ROOT / "examples/pr-text/assessment.toml",
        "--output",
        report,
        "--experiment-output",
        tmp_path / "artifact.json",
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(report.read_text())["verdict"] == verdict
    assert "--reviewer" not in cli("assess", "--help").stdout


def test_cli_protects_rubric_and_rejects_empty_ruleset(tmp_path):
    path = tmp_path / "rubric.json"
    path.write_text('{"rubric_version":1,"criteria":[]}')
    completed = cli(
        "assess", "--input", ROOT / "examples/pr-text/valid.json", "--assessment-rubric", path
    )
    assert completed.returncode == 2
    path.write_text(rubric().model_dump_json())
    before = path.read_bytes()
    completed = cli(
        "assess",
        "--input",
        ROOT / "examples/pr-text/valid.json",
        "--assessment-rubric",
        path,
        "--output",
        path,
    )
    assert completed.returncode == 2
    assert path.read_bytes() == before


def test_deterministic_failure_and_missing_evidence_are_retained_together():
    item = evidence().model_copy(update={"title": "invalid", "files_complete": False})
    report = assess_pull_request(item, Policy(title_format="conventional_commit"), rubric())
    assert report.verdict == "rejected"
    assert report.blockers
    assert "preflight:coverage" in report.unresolved


@pytest.mark.parametrize(
    "command,verdict",
    [
        (["missing-communication-policy-command"], "unresolved"),
        ([sys.executable, "-c", "raise SystemExit(1)"], "rejected"),
        ([sys.executable, "-c", "import time; time.sleep(5)"], "unresolved"),
    ],
)
def test_failed_check_execution_is_distinct_from_failed_policy(command, verdict):
    policy = Policy(
        nonsemantic_checks=[
            {
                "check_id": "policy",
                "description": "Local gate",
                "kind": "script",
                "command": command,
            }
        ],
        check_timeout_seconds=1,
    )
    formal = load_assessment_rubric(ROOT / "examples/pr-text/assessment.toml")
    report = assess_pull_request(evidence(), policy, formal)
    assert report.verdict == verdict


def test_earlier_unknown_rule_cannot_be_bypassed_by_later_matching_rule():
    raw = conditional_rubric().model_dump(mode="json")
    raw["enforcement_rules"][1]["when"] = {"accuracy": ["passed"]}
    config = AssessmentRubric.model_validate(raw)
    provider = Static(
        {"title": "passed", "accuracy": "passed", "testing": "passed", "scale": "uncertain"},
        {"scale": "unknown", "accuracy": "passed"},
    )
    report = assess_pull_request(evidence(), Policy(), config, provider)
    assert report.verdict == "unresolved"
    assert report.selected_enforcement_rule is None


def test_provider_cannot_overwrite_a_deterministic_criterion():
    item = evidence().model_copy(update={"title": "invalid"})
    provider = Static(
        {"title": "passed", "accuracy": "passed", "testing": "passed", "scale": "passed"},
        {"scale": "small"},
    )
    report = assess_pull_request(item, Policy(), conditional_rubric(), provider)
    assert report.verdict == "rejected"
    assert report.criteria[0].status == "failed"
    assert report.criteria[0].source == "deterministic"


def test_known_formal_blocker_prevents_even_a_custom_provider_call():
    calls = []

    class Provider:
        def assess(self, _):
            calls.append(True)
            raise AssertionError("must not be called")

    report = assess_pull_request(
        evidence().model_copy(update={"title": "invalid"}), Policy(), rubric(), Provider()
    )
    assert report.verdict == "rejected"
    assert calls == []


def test_provider_cannot_pass_an_unavailable_semantic_assessment():
    provider = Static({"title": "passed", "accuracy": "passed", "testing": "passed"})
    provider.batch.criteria[1] = provider.batch.criteria[1].model_copy(
        update={"source": "unavailable"}
    )
    report = assess_pull_request(evidence(), Policy(), rubric(), provider)
    assert report.verdict == "unresolved"


@pytest.mark.integration
def test_cli_replays_decider_only_even_with_missing_catalog_reviewer(tmp_path):
    run, _ = execute(record=True)
    tape, data, config, catalog = [
        tmp_path / name for name in ("tape.json", "evidence.json", "rubric.json", "models.toml")
    ]
    tape.write_text(run.tape.model_dump_json())
    data.write_text(evidence().model_dump_json())
    config.write_text(rubric().model_dump_json())
    selected = fixture_config().decision
    catalog.write_text(
        '[defaults]\ndecider="decision"\nreviewer="missing"\n'
        f'[models.decision]\nbackend="ollaya"\nendpoint="{selected.endpoint}"\nmodel="{selected.model}"\n'
    )
    completed = cli(
        "assess",
        "--input",
        data,
        "--assessment-rubric",
        config,
        "--models-config",
        catalog,
        "--replay",
        tape,
        "--experiment-output",
        tmp_path / "artifact.json",
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["verdict"] == "accepted"
