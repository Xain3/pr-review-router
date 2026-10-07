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

from pr_review_router.assessment_rubric import AssessmentCriterion, load_assessment_rubric
from pr_review_router.config import Policy
from pr_review_router.experiments import evaluate_case, run_experiment
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
