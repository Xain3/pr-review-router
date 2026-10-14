"""Offline behavior tests for categorical, probabilistic, and ordinal rubrics."""

import json
import subprocess
import sys

import httpx
import pytest
from local_model_helpers import fixture_config
from pydantic import ValidationError
from rubric_model_helpers import EXAMPLES, RubricServer
from test_assessment_rubric import inputs

from pr_review_router.assessment_rubric import (
    AssessmentCriterion,
    ChoiceOption,
    ChoiceRoutingRule,
    load_assessment_rubric,
)
from pr_review_router.config import Policy
from pr_review_router.experiments import evaluate_case, run_experiment
from pr_review_router.provider_config import DecisionSettings
from pr_review_router.transport import ReplayMismatch

pytestmark = pytest.mark.unit


def numeric_run(kind, value):
    """Override one native answer while exercising real adapter normalization.

    :param kind: Numeric question type to exercise.
    :param value: Native value returned for the testing criterion.
    :returns: Experiment result with its parsed native answer.
    """
    _, evidence, _, review = inputs()
    rubric = load_assessment_rubric(EXAMPLES / f"rubric-{kind}.toml")
    server = RubricServer()

    def response(request):
        """Replace a synthetic native value without bypassing HTTP validation.

        :param request: Actual outgoing request.
        :returns: Synthetic HTTP response.
        """
        result = server.handle(request)
        if request.url.path == "/v1/systemone":
            body = result.json()
            body["answers"]["testing-explanation"][kind] = value
            return httpx.Response(200, json=body)
        return result

    return run_experiment(
        evidence,
        Policy(),
        fixture_config(),
        rubric=rubric,
        review_text=review,
        http_transport=httpx.MockTransport(response),
    )


@pytest.mark.parametrize("kind", ["choice", "noul", "score"])
def test_semantic_question_instructions_include_the_criterion_description(kind):
    """Ensure every semantic question tells the provider which criterion to assess.

    :param kind: Rubric question encoding whose generated instructions are inspected.
    """
    _, evidence, _, review = inputs()
    filename = "rubric.toml" if kind == "choice" else f"rubric-{kind}.toml"
    rubric = load_assessment_rubric(EXAMPLES / filename)
    server = RubricServer()
    run_experiment(
        evidence,
        Policy(),
        fixture_config(),
        rubric=rubric,
        review_text=review,
        http_transport=httpx.MockTransport(server.handle),
    )

    request = next(req for req in server.requests if req.url.path == "/v1/systemone")
    questions = json.loads(request.content)["questions"]
    semantic_criteria = [item for item in rubric.criteria if item.check == "semantic"]
    assert all(
        criterion.description in questions[criterion.criterion_id]["instructions"]
        for criterion in semantic_criteria
    )


@pytest.mark.parametrize(
    "kind,value,status",
    [
        ("noul", 0.2, "failed"),
        ("noul", 0.2001, "uncertain"),
        ("noul", 0.7999, "uncertain"),
        ("noul", 0.8, "passed"),
        ("score", 1.0, "failed"),
        ("score", 1.0001, "uncertain"),
        ("score", 2.7499, "uncertain"),
        ("score", 2.75, "passed"),
    ],
)
def test_numeric_bands_preserve_native_values_and_have_explicit_boundaries(kind, value, status):
    result = numeric_run(kind, value)
    assessed = result.artifact["assessment_rubric"]
    item = assessed["criteria"][-1]
    assert item["status"] == status
    assert item["native_answer"][kind] == value
    assert item["confidence"] == {"value": 0.0, "source": "unavailable", "calibration_id": None}
    assert (
        assessed["recommendation"]
        == {
            "failed": "suggest_changes",
            "uncertain": "needs_human_review",
            "passed": "skip_and_approve",
        }[status]
    )
    assert not assessed["automation_authorized"]


@pytest.mark.parametrize(
    "changes",
    [
        {"question_type": "noul"},
        {"question_type": "noul", "fail_threshold": 0.8, "pass_threshold": 0.2},
        {"question_type": "noul", "fail_threshold": 0.5, "pass_threshold": 0.5},
        {"question_type": "noul", "fail_threshold": 0.2, "pass_threshold": 1.1},
        {"question_type": "noul", "fail_threshold": float("nan"), "pass_threshold": 0.8},
        {
            "question_type": "noul",
            "fail_threshold": 0.2,
            "pass_threshold": 0.8,
            "levels": ["a", "b"],
        },
        {"question_type": "score", "fail_threshold": 0, "pass_threshold": 1},
        {"question_type": "score", "fail_threshold": 0, "pass_threshold": 1, "levels": ["a", "a"]},
        {"question_type": "score", "fail_threshold": 0, "pass_threshold": 2, "levels": ["a", "b"]},
        {"fail_threshold": 0.2, "pass_threshold": 0.8},
    ],
)
def test_invalid_numeric_configuration_is_rejected(changes):
    _, _, rubric, _ = inputs()
    criterion = rubric.criteria[2].model_dump()
    criterion.update(changes)
    with pytest.raises(ValidationError):
        AssessmentCriterion.model_validate(criterion)


@pytest.mark.parametrize(
    "choices",
    [
        [
            {"label": "satisfied", "description": "Satisfied.", "status": "passed"},
            {"label": "satisfied", "description": "Missing.", "status": "failed"},
            {"label": "ambiguous", "description": "Ambiguous.", "status": "uncertain"},
        ],
    ],
)
def test_invalid_choice_configuration_is_rejected(choices):
    """Require distinct provider labels while permitting optional status mappings."""
    _, _, rubric, _ = inputs()
    criterion = rubric.criteria[2].model_dump()
    criterion["choices"] = choices
    with pytest.raises(ValidationError):
        AssessmentCriterion.model_validate(criterion)


@pytest.mark.parametrize(
    "rule",
    [
        {
            "target_criterion_id": "title-format",
            "when": {"change-rationale": ["passed"]},
            "status": "failed",
        },
        {
            "target_criterion_id": "change-rationale",
            "when": {"title-format": ["passed"]},
            "status": "failed",
        },
        {
            "target_criterion_id": "change-rationale",
            "when": {"change-rationale": ["unknown"]},
            "status": "failed",
        },
    ],
)
def test_invalid_choice_routing_rule_is_rejected(rule):
    """Require routing rules to target and inspect configured semantic choices."""
    _, _, rubric, _ = inputs()
    document = rubric.model_dump()
    document["routing_rules"] = [rule]
    with pytest.raises(ValidationError):
        type(rubric).model_validate(document)


def test_formal_choice_criteria_cannot_configure_provider_labels():
    """Keep custom provider choices separate from deterministic formal checks."""
    _, _, rubric, _ = inputs()
    criterion = rubric.criteria[0].model_dump()
    criterion["choices"] = [
        {"label": "satisfied", "description": "Satisfied.", "status": "passed"},
        {"label": "missing", "description": "Missing.", "status": "failed"},
        {"label": "ambiguous", "description": "Ambiguous.", "status": "uncertain"},
    ]
    with pytest.raises(ValidationError):
        AssessmentCriterion.model_validate(criterion)


def test_custom_choice_labels_are_sent_validated_and_normalized_to_routing_statuses():
    """Map custom provider labels to stable passed, failed, and uncertain statuses."""
    _, evidence, rubric, review = inputs()
    rubric.criteria[2].choices = [
        ChoiceOption(label="satisfied", description="The rationale is complete.", status="passed"),
        ChoiceOption(label="missing", description="The rationale is absent.", status="failed"),
        ChoiceOption(
            label="ambiguous",
            description="The evidence cannot establish a rationale.",
            status="uncertain",
        ),
    ]
    server = RubricServer()

    def response(request):
        """Return a custom categorical answer for the criterion being customized.

        :param request: Actual outgoing request.
        :returns: Synthetic HTTP response with the configured native labels.
        """
        result = server.handle(request)
        if request.url.path != "/v1/systemone":
            return result
        body = result.json()
        body["answers"]["change-rationale"].update(
            {
                "choice": "satisfied",
                "probabilities": {"satisfied": 0.99, "missing": 0.005, "ambiguous": 0.005},
            }
        )
        return httpx.Response(200, json=body)

    result = run_experiment(
        evidence,
        Policy(),
        fixture_config(),
        rubric=rubric,
        review_text=review,
        http_transport=httpx.MockTransport(response),
    )
    request = next(request for request in server.requests if request.url.path == "/v1/systemone")
    question = json.loads(request.content)["questions"]["change-rationale"]
    assessment = result.artifact["assessment_rubric"]["criteria"][2]
    assert question["criteria"] == {
        "satisfied": "The rationale is complete.",
        "missing": "The rationale is absent.",
        "ambiguous": "The evidence cannot establish a rationale.",
    }
    assert assessment["status"] == "passed"
    assert assessment["native_answer"]["choice"] == "satisfied"
    assert assessment["explanation"].startswith("Model selected satisfied, which maps to passed.")


def test_unmapped_choices_support_ordered_multi_criterion_routing_rules():
    """Route an unmapped native label after inspecting multiple criterion answers."""
    _, evidence, rubric, review = inputs()
    rationale = rubric.criteria[2]
    coverage = rubric.criteria[3]
    rationale.choices = [
        ChoiceOption(label="complete", description="All rationale is present.", status="passed"),
        ChoiceOption(label="partial", description="Only some rationale is present."),
        ChoiceOption(label="ambiguous", description="Evidence is ambiguous.", status="uncertain"),
    ]
    coverage.choices = [
        ChoiceOption(label="complete", description="All changes are covered.", status="passed"),
        ChoiceOption(
            label="partial", description="Only some changes are covered.", status="failed"
        ),
        ChoiceOption(label="ambiguous", description="Evidence is ambiguous.", status="uncertain"),
    ]
    rubric.routing_rules = [
        ChoiceRoutingRule(
            target_criterion_id="change-rationale",
            when={
                "change-rationale": ["partial"],
                "description-coverage": ["complete"],
            },
            status="failed",
        )
    ]
    server = RubricServer()

    def response(request):
        """Return native labels that exercise the composite routing rule.

        :param request: Actual outgoing request.
        :returns: Synthetic HTTP response with configured native labels.
        """
        result = server.handle(request)
        if request.url.path != "/v1/systemone":
            return result
        body = result.json()
        body["answers"]["change-rationale"].update(
            {
                "choice": "partial",
                "probabilities": {"complete": 0.005, "partial": 0.99, "ambiguous": 0.005},
            }
        )
        body["answers"]["description-coverage"].update(
            {
                "choice": "complete",
                "probabilities": {"complete": 0.99, "partial": 0.005, "ambiguous": 0.005},
            }
        )
        return httpx.Response(200, json=body)

    result = run_experiment(
        evidence,
        Policy(),
        fixture_config(),
        rubric=rubric,
        review_text=review,
        http_transport=httpx.MockTransport(response),
    )
    assessment = result.artifact["assessment_rubric"]
    rationale_result = assessment["criteria"][2]
    assert rationale_result["native_answer"]["choice"] == "partial"
    assert rationale_result["status"] == "failed"
    assert "has no direct status mapping" in rationale_result["explanation"]
    assert "Routing rule set status to failed" in rationale_result["explanation"]
    assert assessment["blockers"] == ["change-rationale"]


def test_choice_routing_does_not_overwrite_an_unanswered_target():
    """Keep an unavailable criterion uncertain when its routing rule conditions match."""
    _, evidence, rubric, _ = inputs()
    rubric.routing_rules = [
        ChoiceRoutingRule(
            target_criterion_id="review-coverage",
            when={"change-rationale": ["passed"]},
            status="passed",
        )
    ]
    server = RubricServer()

    result = run_experiment(
        evidence,
        Policy(),
        fixture_config(),
        rubric=rubric,
        review_text="",
        http_transport=httpx.MockTransport(server.handle),
    )

    assessment = result.artifact["assessment_rubric"]
    review = next(
        item for item in assessment["criteria"] if item["criterion_id"] == "review-coverage"
    )
    assert review["status"] == "uncertain"
    assert review["source"] == "unavailable"
    assert "Routing rule set" not in review["explanation"]
    assert not assessment["approval_recommended"]


def test_provider_choice_capability_allows_extra_labels_with_shared_routing_statuses():
    """Allow rich categorical answers when the selected decision model supports them."""
    _, evidence, rubric, review = inputs()
    rubric.criteria[2].choices = [
        ChoiceOption(label="complete", description="All rationale is present.", status="passed"),
        ChoiceOption(label="partial", description="Some rationale is present.", status="failed"),
        ChoiceOption(label="missing", description="No rationale is present.", status="failed"),
        ChoiceOption(label="ambiguous", description="Evidence is ambiguous.", status="uncertain"),
    ]
    server = RubricServer()
    config = fixture_config().model_copy(
        update={"decision": fixture_config().decision.model_copy(update={"max_choice_options": 4})}
    )

    def response(request):
        """Return an extra provider-visible label that maps to a failed status.

        :param request: Actual outgoing request.
        :returns: Synthetic HTTP response with the configured native labels.
        """
        result = server.handle(request)
        if request.url.path != "/v1/systemone":
            return result
        body = result.json()
        body["answers"]["change-rationale"].update(
            {
                "choice": "partial",
                "probabilities": {
                    "complete": 0.005,
                    "partial": 0.99,
                    "missing": 0.0025,
                    "ambiguous": 0.0025,
                },
            }
        )
        return httpx.Response(200, json=body)

    result = run_experiment(
        evidence,
        Policy(),
        config,
        rubric=rubric,
        review_text=review,
        http_transport=httpx.MockTransport(response),
    )
    assessment = result.artifact["assessment_rubric"]["criteria"][2]
    assert assessment["status"] == "failed"
    assert assessment["native_answer"]["choice"] == "partial"


def test_provider_choice_capability_ignores_formal_criteria():
    """Allow formal criteria when all semantic choices fit the provider limit."""
    _, evidence, rubric, review = inputs()
    for criterion in rubric.criteria:
        if criterion.check == "semantic" and criterion.question_type == "choice":
            criterion.choices = [
                ChoiceOption(label="passed", description="The criterion is satisfied."),
                ChoiceOption(label="failed", description="The criterion is not satisfied."),
            ]
    config = fixture_config()
    config = config.model_copy(
        update={
            "decision": config.decision.model_copy(update={"max_choice_options": 2}),
        }
    )
    server = RubricServer()

    def response(request):
        """Return a valid native response for the configured two-choice questions.

        :param request: Actual outgoing request.
        :returns: Synthetic HTTP response with only configured choice labels.
        """
        result = server.handle(request)
        if request.url.path != "/v1/systemone":
            return result
        body = result.json()
        for answer in body["answers"].values():
            answer["probabilities"] = {"passed": 0.99, "failed": 0.01}
        return httpx.Response(200, json=body)

    result = run_experiment(
        evidence,
        Policy(),
        config,
        rubric=rubric,
        review_text=review,
        http_transport=httpx.MockTransport(response),
    )

    assert server.decision_calls == 1
    assert result.artifact["assessment_rubric"]["criteria"][0]["source"] == "deterministic"


def test_provider_choice_capability_rejects_unsupported_rubric_before_http():
    """Reject a rubric exceeding the selected decision model's advertised limit."""
    _, evidence, rubric, review = inputs()
    rubric.criteria[2].choices = [
        ChoiceOption(label="complete", description="All rationale is present.", status="passed"),
        ChoiceOption(label="partial", description="Some rationale is present.", status="failed"),
        ChoiceOption(label="missing", description="No rationale is present.", status="failed"),
        ChoiceOption(label="ambiguous", description="Evidence is ambiguous.", status="uncertain"),
    ]
    server = RubricServer()
    with pytest.raises(ValueError, match="supports at most 3 choice options"):
        run_experiment(
            evidence,
            Policy(),
            fixture_config(),
            rubric=rubric,
            review_text=review,
            http_transport=httpx.MockTransport(server.handle),
        )
    assert server.requests == []


@pytest.mark.parametrize("value", [1, 101])
def test_decision_choice_capability_rejects_values_outside_supported_range(value):
    """Keep the configured TypeSafe-compatible capability within 2 through 100."""
    settings = fixture_config().decision.model_dump()
    settings["max_choice_options"] = value
    with pytest.raises(ValidationError):
        DecisionSettings.model_validate(settings)


@pytest.mark.parametrize("fault", ["type", "noul_range", "score_range", "legend", "labels"])
@pytest.mark.parametrize("recover", [True, False])
def test_numeric_wire_errors_retry_once_and_never_bypass_validation(fault, recover):
    _, evidence, _, review = inputs()
    rubric = load_assessment_rubric(EXAMPLES / "rubric-mixed.toml")
    server = RubricServer()

    def response(request):
        """Inject a malformed first answer or repeat the failure on the strict retry.

        :param request: Actual outgoing request.
        :returns: Native response with the configured validation fault.
        """
        result = server.handle(request)
        if request.url.path != "/v1/systemone" or (recover and server.decision_calls > 1):
            return result
        body = result.json()
        if fault == "type":
            body["answers"]["description-coverage"] = {"type": "noul", "noul": 0.9}
            body["answers"]["change-rationale"] = {"type": "noul", "noul": 0.9}
        elif fault == "noul_range":
            body["answers"]["description-coverage"]["noul"] = 1.1
        elif fault == "score_range":
            body["answers"]["change-rationale"]["score"] = 4.0
        elif fault == "legend":
            body["answers"]["change-rationale"]["legend"]["0"] = "Unexpected scale"
        else:
            body["answers"]["change-rationale"]["probabilities"]["extra"] = 0.0
        return httpx.Response(200, json=body)

    result = run_experiment(
        evidence,
        Policy(),
        fixture_config(),
        rubric=rubric,
        review_text=review,
        http_transport=httpx.MockTransport(response),
    )
    assert server.decision_calls == 2
    assert result.artifact["schema_failures"] == (1 if recover else 2)
    assert result.artifact["assessment_rubric"]["approval_recommended"] == recover
    assert server.review_calls == (1 if recover else 0)


def test_mixed_native_types_preserve_independent_labels_and_invalidate_changed_thresholds(tmp_path):
    case, evidence, _, review = inputs(3)
    rubric = load_assessment_rubric(EXAMPLES / "rubric-mixed.toml")
    server = RubricServer()
    result = run_experiment(
        evidence,
        Policy(),
        fixture_config(),
        rubric=rubric,
        review_text=review,
        record=True,
        http_transport=httpx.MockTransport(server.handle),
    )
    metrics = evaluate_case(case, result)
    assert metrics["unsafe_rubric_approvals"] == 1
    assert metrics["rubric"]["native_answers"]["change-rationale"]["type"] == "score"
    assert metrics["rubric"]["native_answers"]["review-coverage"]["type"] == "noul"
    tape = tmp_path / "tape.json"
    assert result.tape is not None
    tape.write_text(result.tape.model_dump_json())
    rubric.criteria[2].pass_threshold = 2.9
    with pytest.raises(ReplayMismatch):
        run_experiment(
            evidence, Policy(), fixture_config(), rubric=rubric, review_text=review, replay=tape
        )


@pytest.mark.integration
def test_three_format_comparison_replays_the_same_nine_case_labels(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(EXAMPLES / "compare.py"), "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    formats = json.loads(completed.stdout)["formats"]
    assert set(formats) == {"choice", "noul", "score"}
    for kind, summary in formats.items():
        assert summary["case_count"] == 9
        assert summary["mode"] == "replay"
        assert summary["totals"]["rubric_criterion_mismatches"] == 0
        assert summary["totals"]["unsafe_rubric_approvals"] == 0
        assert set(summary["cases"][0]["rubric"]["question_types"].values()) == {kind}
