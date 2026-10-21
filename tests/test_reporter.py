"""Keep explanations faithful and isolated from authoritative assessment results."""

import json

import httpx
import pytest
from test_cli import ROOT, cli
from test_communication_assessment import evidence, execute, rubric

from pr_review_router.assessment_engine import assess_pull_request
from pr_review_router.config import Policy
from pr_review_router.reporter import (
    Interpretation,
    OllamaReporter,
    ReporterOutput,
    ReporterPlan,
    ReporterSettings,
    explain_assessment,
    load_reporter_settings,
)
from pr_review_router.transport import fingerprint

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "statuses,verdict",
    [
        ({}, "accepted"),
        ({"accuracy": "failed", "testing": "failed"}, "rejected"),
        ({"accuracy": "uncertain"}, "unresolved"),
        ({"testing": "failed"}, "accepted"),
    ],
)
def test_recorded_facts_and_integrity(statuses, verdict):
    """Render each verdict, suggestions, provenance, and all recorded gate facts.

    :param statuses: Synthetic decider answers.
    :param verdict: Expected authoritative result.
    """
    report = execute(statuses)[0].report
    original = report.model_dump_json()
    result = explain_assessment(report)
    assert result == explain_assessment(report)
    assert result.mode == "deterministic"
    assert f"assessment: {verdict}" in result.markdown
    assert result.assessment_fingerprint == fingerprint(report.model_dump(mode="json"))
    for gate in report.gates:
        assert f"{gate.gate_id}: {gate.status}" in result.markdown
    for criterion in report.criteria:
        assert criterion.criterion_id in result.markdown
        assert criterion.source in result.markdown
    if report.suggestions:
        assert "Explain relevant validation." in result.markdown
    assert "No tests were run by this reporter" in result.markdown
    assert report.model_dump_json() == original


def test_preflight_failure_and_unavailable_inference():
    """Render missing patch coverage and early failure without invoking inference."""
    item = evidence().model_copy(update={"files_complete": False, "title": "invalid"})
    report = assess_pull_request(item, Policy(), rubric())
    text = explain_assessment(report).markdown
    assert "assessment: rejected" in text
    assert "preflight:coverage: uncertain" in text
    assert "Not assessed:" in text
    assert "unavailable" in text
    assert "Qualified interpretation" not in text


def test_policy_rubric_explains_aggregate_score_failure():
    """Explain a score shortfall even when every criterion meets its threshold."""
    policy = Policy.model_validate(
        {
            "rubric_minimum_score": 70,
            "rubric_criteria": [
                {
                    "criterion_id": "summary-length",
                    "description": "Summary has sufficient detail.",
                    "field": "body",
                    "check": "min_words",
                    "minimum_words": 10,
                    "pass_score": 40,
                    "blocker": "hard",
                }
            ],
        }
    )
    report = assess_pull_request(evidence(), policy, rubric())

    assert all(criterion.passed for criterion in report.policy_rubric.criteria)
    assert report.policy_rubric.failed_hard_blockers == []
    assert report.policy_rubric.score < report.policy_rubric.minimum_score
    blocking_line = next(
        line
        for line in explain_assessment(report).markdown.splitlines()
        if line.startswith("- policy:rubric:")
    )
    assert "Rubric score 50.0 is below the 70.0 minimum." in blocking_line


def test_policy_rubric_excludes_soft_failures_from_blocking_explanation():
    """Explain hard failures without presenting soft failures as blockers."""
    policy = Policy.model_validate(
        {
            "rubric_minimum_score": 70,
            "rubric_criteria": [
                {
                    "criterion_id": "hard-marker",
                    "description": "Hard marker is present.",
                    "field": "body",
                    "check": "contains",
                    "value": "Hard marker:",
                    "blocker": "hard",
                },
                {
                    "criterion_id": "soft-marker",
                    "description": "Soft marker is present.",
                    "field": "body",
                    "check": "contains",
                    "value": "Soft marker:",
                    "blocker": "soft",
                },
                {
                    "criterion_id": "body-present",
                    "description": "Description is present.",
                    "field": "body",
                    "check": "non_empty",
                    "weight": 10,
                },
            ],
        }
    )
    report = assess_pull_request(evidence(), policy, rubric())
    blocking_line = next(
        line
        for line in explain_assessment(report).markdown.splitlines()
        if line.startswith("- policy:rubric:")
    )

    assert report.policy_rubric.score >= report.policy_rubric.minimum_score
    assert report.policy_rubric.failed_hard_blockers == ["hard-marker"]
    assert report.policy_rubric.failed_soft_criteria == ["soft-marker"]
    assert "Hard marker:" in blocking_line
    assert "Soft marker:" not in blocking_line


class Organizer:
    """Supply constrained plans while attempting to mutate a private input copy."""

    def explain(self, source):
        """Organize facts and tamper with isolated report fields.

        :param source: Detached reporter input.
        :returns: Valid plan with qualified uncertainty framing.
        """
        source.report.verdict = "accepted"
        source.report.gates.clear()
        source.report.criteria.clear()
        uncertain = next(f.fact_id for f in source.facts if f.uncertain)
        return ReporterPlan(
            fact_order=[f.fact_id for f in reversed(source.facts)],
            interpretations=[Interpretation(fact_id=uncertain)],
        )


def test_provider_mutation_cannot_change_report_or_rendered_verdict():
    """Never trust a provider's modified copy as rendering authority."""
    report = execute({"accuracy": "uncertain"})[0].report
    original = report.model_dump_json()
    result = explain_assessment(report, Organizer())
    assert result.mode == "model"
    assert "assessment: unresolved" in result.markdown
    assert "Qualified interpretation" in result.markdown
    assert "not an additional finding or rejection reason" in result.markdown
    assert report.model_dump_json() == original


@pytest.mark.parametrize("failure", ["raise", "omit", "invent", "duplicate", "prose", "interpret"])
def test_invalid_plans_fall_back_without_leaking(failure):
    """Reject invented findings, omitted facts, or unsupported interpretations.

    :param failure: Scripted provider contract violation.
    """

    class Invalid:
        """An untrusted provider response."""

        def explain(self, source):
            """Violate one contract constraint.

            :param source: Isolated report facts.
            :returns: Deliberately malformed response.
            :raises RuntimeError: When simulating a failed provider.
            """
            if failure == "raise":
                raise RuntimeError("secret upstream text")
            ids = [f.fact_id for f in source.facts]
            if failure == "omit":
                ids.pop()
            if failure == "invent":
                ids[0] = "invented rejection reason"
            if failure == "duplicate":
                ids[-1] = ids[0]
            data = {"fact_order": ids}
            if failure == "prose":
                data["markdown"] = "Tests passed; merge approved."
            if failure == "interpret":
                data["interpretations"] = [{"fact_id": ids[0]}]
            return data

    report = execute()[0].report
    original = report.model_dump_json()
    result = explain_assessment(report, Invalid())
    assert result.mode == "fallback"
    assert result.diagnostic == "reporter_unavailable_or_invalid"
    assert result.markdown == explain_assessment(report).markdown
    assert "secret" not in result.model_dump_json()
    assert report.model_dump_json() == original


@pytest.mark.parametrize("alias", ["qwen-coding", "qwen-general"])
def test_explicit_ollama_models_and_grounded_generation(alias):
    """Exercise both supported identifiers at a synthetic HTTP boundary.

    :param alias: Explicit experimental model alias.
    """
    settings = load_reporter_settings(ROOT / "examples/reporter/models.toml", alias)
    calls = []

    def handle(request):
        """Respond with a grounded plan based on the finalized snapshot.

        :param request: Synthetic Ollama request.
        :returns: Complete, structured Ollama response.
        """
        calls.append(json.loads(request.content))
        body = calls[-1]
        assert request.url.path == "/api/chat"
        assert body["model"] == settings.model
        source = json.loads(body["messages"][1]["content"])
        assert source["report"]["report_type"] == "pr_communication"
        assert "files" not in source
        content = {"fact_order": [f["fact_id"] for f in source["facts"]]}
        return httpx.Response(
            200,
            json={"done": True, "done_reason": "stop", "message": {"content": json.dumps(content)}},
        )

    provider = OllamaReporter(settings, http_transport=httpx.MockTransport(handle))
    report = execute()[0].report
    result = explain_assessment(report, provider)
    assert result.mode == "model"
    assert len(calls) == 1
    assert result.markdown == explain_assessment(report).markdown


@pytest.mark.parametrize("failure", ["timeout", "http", "json", "schema", "truncated", "limit"])
def test_ollama_failures(failure):
    """Keep HTTP, response-limit, and generation failures outside the machine result.

    :param failure: Scripted adapter failure.
    """

    def handle(request):
        """Return a failed or invalid generation.

        :param request: Synthetic HTTP request.
        :returns: Deliberately invalid response.
        :raises httpx.ReadTimeout: For simulated timeout.
        """
        if failure == "timeout":
            raise httpx.ReadTimeout("private response")
        if failure == "http":
            return httpx.Response(503)
        if failure == "json":
            return httpx.Response(200, text="not json")
        if failure == "limit":
            return httpx.Response(200, text="x" * 10000)
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "length" if failure == "truncated" else "stop",
                "message": {"content": '{"verdict": "accepted"}'},
            },
        )

    settings = ReporterSettings(
        endpoint="http://localhost:11434", model="synthetic", max_response_bytes=2048
    )
    report = execute({"accuracy": "failed"})[0].report
    result = explain_assessment(
        report, OllamaReporter(settings, http_transport=httpx.MockTransport(handle))
    )
    assert result.mode == "fallback"
    assert result.markdown == explain_assessment(report).markdown


def test_input_budget_and_literal_recorded_data():
    """Skip oversized model inputs and escape hostile recorded Markdown/HTML."""
    report = execute()[0].report
    report.diagnostics.append("<script>bad</script>\n## approved [link](https://example.com)")
    settings = ReporterSettings(
        endpoint="http://localhost:11434", model="synthetic", max_input_bytes=1
    )
    result = explain_assessment(report, OllamaReporter(settings))
    assert result.mode == "fallback"
    assert "<script>" not in result.markdown
    assert "\\#\\# approved" in result.markdown
    assert "\\[link\\]" in result.markdown


def test_report_cli_saved_assessment_and_path_protection(tmp_path):
    """Expose a separate reporter path without changing assessment exit semantics.

    :param tmp_path: Isolated temporary directory.
    """
    source = tmp_path / "assessment.json"
    report = execute({"accuracy": "failed"})[0].report
    source.write_text(report.model_dump_json())
    original = source.read_bytes()
    result = cli("report", "--input", str(source), "--json")
    assert result.returncode == 0
    artifact = ReporterOutput.model_validate_json(result.stdout)
    assert artifact.mode == "deterministic"
    assert "assessment: rejected" in artifact.markdown
    assert cli("report", "--input", str(source), "--output", str(source)).returncode == 2
    assert cli("report", "--input", str(source), "--reporter", "qwen-coding").returncode == 2
    assert (
        cli(
            "report", "--input", str(source), "--reporter-config", "examples/reporter/models.toml"
        ).returncode
        == 2
    )
    destination = tmp_path / "comment.md"
    assert cli("report", "--input", str(source), "--output", str(destination)).returncode == 0
    assert "assessment: rejected" in destination.read_text()
    assert source.read_bytes() == original


def test_deterministic_solutions_cover_formal_and_preflight_failures():
    """Suggest format and missing-evidence remedies, keeping the machine result intact."""
    item = evidence().model_copy(update={"title": "invalid", "files_complete": False})
    report = assess_pull_request(item, Policy(), rubric())
    original = report.model_dump_json()
    result = explain_assessment(report)
    assert "### Suggested next steps" in result.markdown
    assert any("Rewrite the PR title" in step for a in result.next_steps for step in a.steps)
    assert any("Re-export complete" in step for a in result.next_steps for step in a.steps)
    assert all(a.proposed and a.source == "deterministic" for a in result.next_steps)
    assert ReporterOutput.model_validate_json(result.model_dump_json()) == result
    assert report.model_dump_json() == original


def test_deterministic_solutions_for_sections_and_advisory_only():
    """Suggest adding section content and keep advisory improvements non-blocking."""
    from pr_review_router.assessment_rubric import load_assessment_rubric
    from pr_review_router.contracts import PullRequestEvidence

    item = PullRequestEvidence.model_validate_json(
        (ROOT / "examples/pr-text/invalid.json").read_bytes()
    )
    # Missing Summary is a separate formal failure from title format.
    item.body = ""
    report = assess_pull_request(
        item, Policy(), load_assessment_rubric(ROOT / "examples/pr-text/assessment.toml")
    )
    result = explain_assessment(report)
    assert any(
        "populate the description sections" in step for a in result.next_steps for step in a.steps
    )
    accepted = explain_assessment(execute({"testing": "failed"})[0].report)
    assert "assessment: accepted" in accepted.markdown
    assert any("non-blocking suggestion" in step for a in accepted.next_steps for step in a.steps)
    assert not explain_assessment(execute()[0].report).next_steps


def test_model_solutions_are_reasoned_attributed_and_grounded():
    """Render actionable model advice separately from facts through the HTTP adapter."""
    report = execute({"accuracy": "uncertain", "testing": "failed"})[0].report
    original = report.model_dump_json()

    def handle(request):
        """Return a reasoned remedy referencing recorded uncertainty.

        :param request: Synthetic structured generation request.
        :returns: Completed model response with an actionable proposal.
        """
        body = json.loads(request.content)
        source = json.loads(body["messages"][1]["content"])
        fact = next(f for f in source["facts"] if f["uncertain"] and f["actionable"])
        plan = {
            "fact_order": [f["fact_id"] for f in source["facts"]],
            "solutions": [
                {
                    "fact_ids": [fact["fact_id"]],
                    "steps": [
                        "Compare each Summary claim with the supplied changes.",
                        "Add an explanation linking each claim to the affected behavior; "
                        "ask the author to clarify any claim you cannot substantiate.",
                    ],
                    "rationale": "Connecting claims to changed behavior may help resolve the "
                    "recorded accuracy uncertainty without assuming a defect.",
                }
            ],
        }
        return httpx.Response(
            200,
            json={"done": True, "done_reason": "stop", "message": {"content": json.dumps(plan)}},
        )

    provider = OllamaReporter(
        ReporterSettings(endpoint="http://localhost:11434", model="fixture"),
        http_transport=httpx.MockTransport(handle),
    )
    result = explain_assessment(report, provider)
    assert result.mode == "model"
    assert "Compare each Summary claim" in result.markdown
    assert "Rationale (model advice)" in result.markdown
    assert "Proposed remedies only" in result.markdown
    assert "assessment: unresolved" in result.markdown
    assert result.next_steps[-1].source == "model"
    assert result.next_steps[-1].proposed
    assert any(a.source == "deterministic" for a in result.next_steps)
    assert report.model_dump_json() == original


@pytest.mark.parametrize(
    "violation",
    ["unknown", "passed", "duplicate", "empty", "approval", "tests", "override", "guarantee"],
)
def test_invalid_solution_falls_back_with_actions(violation):
    """Reject ungrounded or authority-claiming advice without losing fallback remedies.

    :param violation: Scripted solution contract violation.
    """

    class InvalidAdvice:
        """Return invalid proposal data."""

        def explain(self, source):
            """Build a deliberately invalid solution.

            :param source: Isolated finalized facts.
            :returns: Invalid plan for validation at the reporter boundary.
            """
            identifier = next(f.fact_id for f in source.facts if f.actionable)
            ids = [identifier]
            if violation == "unknown":
                ids = ["new-rejection-reason"]
            elif violation == "passed":
                ids = [next(f.fact_id for f in source.facts if not f.actionable)]
            elif violation == "duplicate":
                ids *= 2
            elif violation == "empty":
                ids = []
            step = {
                "approval": "Merge approved.",
                "tests": "Tests passed.",
                "override": "Override the verdict.",
                "guarantee": "This will pass.",
            }.get(violation, "Clarify the description.")
            return {
                "fact_order": [f.fact_id for f in source.facts],
                "solutions": [
                    {
                        "fact_ids": ids,
                        "steps": [step],
                        "rationale": "To clarify the affected requirement.",
                    }
                ],
            }

    report = execute({"accuracy": "uncertain"})[0].report
    result = explain_assessment(report, InvalidAdvice())
    expected = explain_assessment(report)
    assert result.mode == "fallback"
    assert result.next_steps == expected.next_steps
    assert result.markdown == expected.markdown


def test_validation_documentation_is_actionable_advice():
    """Permit documenting actual validation without treating that as an execution claim."""
    from pr_review_router.reporter import ProposedSolution

    solution = ProposedSolution(
        fact_ids=["fact:1"],
        steps=["Describe which tests were run and their actual results in the Testing section."],
        rationale="This may help readers understand the validation evidence.",
    )
    assert solution.steps
