"""Scale-driven enforcement rules for experiment assessment rubrics."""

import httpx
import pytest
from local_model_helpers import fixture_config
from pydantic import ValidationError
from rubric_model_helpers import RubricServer
from test_assessment_rubric import inputs

from pr_review_router.assessment_rubric import (
    AssessmentRubric,
    CriterionAssessment,
    combine_assessments,
)
from pr_review_router.config import Policy
from pr_review_router.experiments import run_experiment

SCALE_LABELS = ["trivial", "minor", "moderate", "substantial", "major"]
KEPT_CRITERIA = {"title-format", "summary-section", "change-rationale", "description-coverage"}
BLOCKING_RULES = [
    {
        "when": {"scale-a": ["trivial"]},
        "enforce": ["title-format", {"any_of": ["summary-section", "description-coverage"]}],
    },
    {
        "when": {"scale-a": ["minor", "moderate"]},
        "enforce": ["title-format", "summary-section", "description-coverage"],
    },
    {
        "when": {"scale-a": ["substantial", "major"]},
        "enforce": [
            "title-format",
            "summary-section",
            "change-rationale",
            "description-coverage",
        ],
    },
]
MIXED_RULES = [
    {
        "when": {"scale-a": ["trivial"]},
        "enforce": ["title-format"],
        "soft_enforce": [{"any_of": ["summary-section", "description-coverage"]}],
    },
    {
        "when": {"scale-a": ["minor", "moderate"]},
        "enforce": ["title-format", "summary-section"],
        "soft_enforce": ["description-coverage"],
    },
    {
        "when": {"scale-a": ["substantial", "major"]},
        "enforce": ["title-format", "summary-section"],
        "soft_enforce": ["change-rationale", "description-coverage"],
    },
]


def scenario_rubric(rules):
    """Build the four-criterion scenario with a five-point triviality criterion.

    :param rules: Enforcement rule mappings to validate with the scenario criteria.
    :returns: Validated rubric, PR evidence, and supplied review text.
    """
    _, evidence, rubric, review = inputs()
    data = rubric.model_dump(mode="json")
    criteria = [item for item in data["criteria"] if item["criterion_id"] in KEPT_CRITERIA]
    for item in criteria:
        del item["action"]
    criteria.append(
        {
            "criterion_id": "scale-a",
            "description": "Triviality of the change on a five-point scale.",
            "check": "semantic",
            "suggestion": "Classify how trivial the change is.",
            "instructions": "Choose the label that best matches how trivial the change is.",
            "target": "description",
            "question_type": "choice",
            "choices": [
                {"label": label, "description": f"Triviality is {label}."} for label in SCALE_LABELS
            ],
        }
    )
    data["criteria"] = criteria
    data["enforcement_rules"] = rules
    return AssessmentRubric.model_validate(data), evidence, review


class ScaleServer(RubricServer):
    """Return one configured triviality label alongside the scripted criterion answers."""

    def __init__(self, scale: str, statuses: dict[str, str]) -> None:
        """Select the native scale label and scripted statuses.

        :param scale: Native triviality label returned for the scale criterion.
        :param statuses: Scripted statuses for the other semantic criteria.
        """
        super().__init__(statuses)
        self.scale = scale

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Replace the default scale answer with a configured triviality label.

        :param request: Actual outgoing request.
        :returns: Synthetic HTTP response including the configured scale label.
        """
        result = super().handle(request)
        if request.url.path != "/v1/systemone":
            return result
        body = result.json()
        probabilities = dict.fromkeys(SCALE_LABELS, 0.01)
        probabilities[self.scale] = 0.96
        body["answers"]["scale-a"].update({"choice": self.scale, "probabilities": probabilities})
        return httpx.Response(200, json=body)


def run_scenario(scale, *, empty_body=False, statuses=None, rules=BLOCKING_RULES):
    """Run the scenario through the experiment pipeline with synthetic native answers.

    :param scale: Native triviality label for the scale criterion.
    :param empty_body: Whether to remove the summary section so its formal check fails.
    :param statuses: Scripted statuses for semantic criteria.
    :param rules: Enforcement rule mappings to validate.
    :returns: Rubric artifact and the intercepted server.
    """
    rubric, evidence, review = scenario_rubric(rules)
    if empty_body:
        evidence = evidence.model_copy(update={"body": ""})
    config = fixture_config()
    config = config.model_copy(
        update={"decision": config.decision.model_copy(update={"max_choice_options": 5})}
    )
    server = ScaleServer(scale, statuses or {})
    result = run_experiment(
        evidence,
        Policy(),
        config,
        rubric=rubric,
        review_text=review,
        http_transport=httpx.MockTransport(server.handle),
    )
    return result.artifact["assessment_rubric"], server


@pytest.mark.parametrize(
    ("scale", "empty_body", "statuses", "recommendation", "blockers"),
    [
        ("trivial", False, {}, "skip_and_approve", []),
        ("trivial", True, {"description-coverage": "passed"}, "skip_and_approve", []),
        (
            "trivial",
            True,
            {"description-coverage": "failed"},
            "block",
            ["summary-section", "description-coverage"],
        ),
        ("trivial", True, {"description-coverage": "uncertain"}, "needs_human_review", []),
        ("trivial", False, {"change-rationale": "failed"}, "skip_and_approve", []),
        ("minor", True, {"description-coverage": "passed"}, "block", ["summary-section"]),
        ("moderate", False, {"change-rationale": "failed"}, "skip_and_approve", []),
        ("substantial", False, {"change-rationale": "failed"}, "block", ["change-rationale"]),
        ("major", False, {"change-rationale": "uncertain"}, "needs_human_review", []),
        ("major", False, {"description-coverage": "failed"}, "block", ["description-coverage"]),
    ],
)
def test_blocking_enforcement_matches_the_user_policy_matrix(
    scale, empty_body, statuses, recommendation, blockers
):
    """Block on failed enforce members, with membership defined only by the rules."""
    artifact, server = run_scenario(scale, empty_body=empty_body, statuses=statuses)
    assert artifact["recommendation"] == recommendation
    assert artifact["blockers"] == blockers
    assert artifact["suggestions"] == []
    assert any(request.url.path == "/v1/systemone" for request in server.requests)


@pytest.mark.parametrize(
    ("scale", "empty_body", "statuses", "recommendation", "blockers", "suggestions"),
    [
        ("trivial", True, {"description-coverage": "passed"}, "skip_and_approve", [], []),
        (
            "trivial",
            True,
            {"description-coverage": "failed"},
            "suggest_changes",
            [],
            ["summary-section", "description-coverage"],
        ),
        ("trivial", True, {"description-coverage": "uncertain"}, "needs_human_review", [], []),
        (
            "minor",
            True,
            {"description-coverage": "failed"},
            "block",
            ["summary-section"],
            ["description-coverage"],
        ),
        ("minor", True, {"description-coverage": "uncertain"}, "block", ["summary-section"], []),
        (
            "substantial",
            False,
            {"change-rationale": "failed"},
            "suggest_changes",
            [],
            ["change-rationale"],
        ),
        ("substantial", False, {"change-rationale": "uncertain"}, "needs_human_review", [], []),
        (
            "major",
            False,
            {"change-rationale": "uncertain", "description-coverage": "failed"},
            "needs_human_review",
            [],
            ["description-coverage"],
        ),
    ],
)
def test_soft_enforcement_suggests_without_blocking(
    scale, empty_body, statuses, recommendation, blockers, suggestions
):
    """Report failed soft members as suggestions while uncertainty still prevents approval."""
    artifact, _ = run_scenario(scale, empty_body=empty_body, statuses=statuses, rules=MIXED_RULES)
    assert artifact["recommendation"] == recommendation
    assert artifact["blockers"] == blockers
    assert [item["criterion_id"] for item in artifact["suggestions"]] == suggestions


def test_formal_failure_is_evaluated_by_enforcement_rules_instead_of_short_circuiting():
    """Allow a passing any-of member to satisfy a failed deterministic member."""
    artifact, server = run_scenario("trivial", empty_body=True)
    by_id = {item["criterion_id"]: item for item in artifact["criteria"]}
    assert by_id["summary-section"]["status"] == "failed"
    assert by_id["title-format"]["status"] == "passed"
    assert by_id["change-rationale"]["explanation"].endswith(
        "Not enforced for this decision; does not affect routing."
    )
    assert artifact["recommendation"] == "skip_and_approve"
    assert any(request.url.path == "/v1/systemone" for request in server.requests)


def test_unmatched_scale_fails_closed_and_enforces_all_non_condition_criteria():
    """Block any failed criterion when no enforcement rule resolves the scale."""
    rules = [BLOCKING_RULES[0]]
    artifact, _ = run_scenario("major", rules=rules)
    assert artifact["recommendation"] == "needs_human_review"
    assert artifact["blockers"] == []
    artifact, _ = run_scenario("major", statuses={"change-rationale": "failed"}, rules=rules)
    assert artifact["recommendation"] == "block"
    assert artifact["blockers"] == ["change-rationale"]
    assert artifact["suggestions"] == []
    artifact, _ = run_scenario("major", statuses={"change-rationale": "uncertain"}, rules=rules)
    assert artifact["recommendation"] == "needs_human_review"
    assert artifact["blockers"] == []
    artifact, _ = run_scenario("major", empty_body=True, rules=rules)
    assert artifact["recommendation"] == "block"
    assert artifact["blockers"] == ["summary-section"]


@pytest.mark.parametrize("criterion_id", ["title-format", "scale-a"])
def test_rules_reject_criterion_action(criterion_id):
    """Forbid per-criterion action when enforcement rules define enforcement."""
    rubric, _, _ = scenario_rubric(BLOCKING_RULES)
    data = rubric.model_dump(mode="json")
    next(item for item in data["criteria"] if item["criterion_id"] == criterion_id)["action"] = (
        "block"
    )
    with pytest.raises(ValidationError, match="not allowed when enforcement_rules"):
        AssessmentRubric.model_validate(data)


def test_legacy_rubric_requires_action_on_every_criterion():
    """Require action for each criterion when enforcement rules are absent."""
    _, _, rubric, _ = inputs()
    data = rubric.model_dump(mode="json")
    del data["criteria"][0]["action"]
    with pytest.raises(ValidationError, match="action is required"):
        AssessmentRubric.model_validate(data)


def test_rubric_without_enforcement_rules_keeps_all_criteria_enforced():
    """Preserve the default behavior and exact fingerprint shape for legacy rubrics."""
    _, _, rubric, _ = inputs()
    assert rubric.enforcement_rules == []
    artifact = combine_assessments(
        rubric,
        [
            CriterionAssessment(
                criterion_id=item.criterion_id,
                status="passed",
                source="deterministic",
                explanation="ok",
            )
            for item in rubric.criteria
        ],
    )
    assert artifact.recommendation == "skip_and_approve"


@pytest.mark.parametrize(
    "rules",
    [
        [{"when": {"scale-a": ["trivial"]}, "enforce": ["unknown-criterion"]}],
        [{"when": {"scale-a": ["trivial"]}, "soft_enforce": ["unknown-criterion"]}],
        [{"when": {"scale-a": ["trivial"]}, "enforce": [{"any_of": ["summary-section"]}]}],
        [
            {
                "when": {"scale-a": ["trivial"]},
                "enforce": ["summary-section", {"any_of": ["summary-section", "title-format"]}],
            }
        ],
        [
            {
                "when": {"scale-a": ["trivial"]},
                "enforce": ["summary-section"],
                "soft_enforce": ["summary-section"],
            }
        ],
        [{"when": {"scale-a": ["trivial"]}}],
        [{"when": {"title-format": ["passed"]}, "enforce": ["summary-section"]}],
        [{"when": {"scale-a": ["unknown-label"]}, "enforce": ["summary-section"]}],
        [{"when": {"scale-a": []}, "enforce": ["summary-section"]}],
    ],
)
def test_invalid_enforcement_rules_are_rejected(rules):
    """Reject unknown references, degenerate groups, duplicates, and non-choice conditions."""
    with pytest.raises(ValidationError):
        scenario_rubric(rules)
