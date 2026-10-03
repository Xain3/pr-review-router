import json
from typing import Literal

import pytest
from pydantic import ValidationError

from pr_review_router.config import Policy
from pr_review_router.contracts import Confidence, Decision, Finding, ReviewReport, ReviewResult
from pr_review_router.engine import review_pull_request
from pr_review_router.providers import MockDecisionProvider, MockReviewProvider

Source = Literal["mock", "self_reported", "calibrated", "unavailable"]
Recommendation = Literal["skip_review", "review", "needs_human_review"]
Outcome = Literal["no_concerns", "concerns", "uncertain"]


class ScriptedDecision:
    def __init__(
        self,
        recommendation: Recommendation = "review",
        value: float = 0.99,
        source: Source = "mock",
    ):
        self.calls = 0
        self.decision = Decision(
            recommendation=recommendation,
            confidence=Confidence(value=value, source=source),
            reason="Scripted decision.",
            provider="test-decision",
        )

    def decide(self, evidence):
        self.calls += 1
        return self.decision


class ScriptedReview:
    def __init__(self, *results):
        self.results = iter(results)
        self.depths = []

    def review(self, evidence, *, depth):
        self.depths.append(depth)
        return next(self.results)


def result(
    outcome: Outcome = "no_concerns",
    value: float = 0.99,
    findings: list[Finding] | None = None,
    source: Source = "mock",
):
    return ReviewResult(
        outcome=outcome,
        confidence=Confidence(value=value, source=source),
        summary="Scripted review.",
        provider="test-review",
        findings=findings or [],
    )


def finding(path="docs/usage.md", title="Concern"):
    return Finding(path=path, line=1, severity="high", title=title, detail="Needs investigation.")


def run(evidence, policy=None, decision=None, reviewer=None):
    return review_pull_request(
        evidence,
        policy or Policy(),
        decision or MockDecisionProvider(),
        reviewer or MockReviewProvider(),
    )


def test_editorial_example_skips_and_report_round_trips(evidence):
    report = run(evidence)
    assert report.outcome == "skipped"
    assert report.route == "no_review"
    assert report.coverage.complete
    assert report.decision is not None
    assert report.decision.confidence.source == "mock"
    assert report.reviews == []
    assert ReviewReport.model_validate_json(report.model_dump_json()) == report
    assert json.loads(report.model_dump_json())["advisory_only"] is True


@pytest.mark.parametrize(
    ("changes", "issue"),
    [
        ({"patch": None}, "missing"),
        ({"patch": ""}, "missing"),
        ({"patch_truncated": True}, "truncated"),
        ({"patch": "@@ -1,2 +1,2 @@\n-old\n+new\n"}, "incomplete"),
        ({"patch": "@@ -1 +1 @@\n-old\n+new\n+extra\n"}, "invalid"),
        ({"patch": "@@ -0 +1 @@\n-old\n+new\n"}, "invalid"),
        ({"patch": "@@ -1 +0 @@\n-old\n+new\n"}, "invalid"),
        ({"additions": 2}, "disagree"),
        ({"patch": "Binary files differ"}, "invalid"),
        ({"patch": "@@ -1 +1 @@\n unchanged\n", "additions": 0, "deletions": 0}, "no textual"),
    ],
)
def test_bad_patch_requires_human_before_any_provider_call(evidence, changes, issue):
    evidence.files[0] = evidence.files[0].model_copy(update=changes)
    decider = ScriptedDecision("skip_review")
    reviewer = ScriptedReview()
    report = run(evidence, decision=decider, reviewer=reviewer)
    assert report.outcome == "needs_human_review"
    assert not report.coverage.complete
    assert any(issue in message for message in report.coverage.issues)
    assert decider.calls == 0
    assert reviewer.depths == []


@pytest.mark.parametrize("case", ["no_files", "partial_files", "input_budget", "file_budget"])
def test_incomplete_or_over_budget_evidence_never_reaches_providers(evidence, case):
    policy = Policy()
    if case == "no_files":
        evidence.files = []
    elif case == "partial_files":
        evidence.files_complete = False
    elif case == "input_budget":
        evidence.body = "é" * 1000
        policy = Policy(max_input_bytes=100)
    else:
        other = evidence.files[0].model_copy(update={"path": "README.md"})
        evidence.files.append(other)
        policy = Policy(max_files=1)
    decider = ScriptedDecision()
    report = run(evidence, policy=policy, decision=decider)
    assert report.outcome == "needs_human_review"
    assert decider.calls == 0


@pytest.mark.parametrize(("value", "skipped"), [(0.95, True), (0.949, False)])
def test_skip_threshold_boundary(evidence, value, skipped):
    reviewer = ScriptedReview(result())
    report = run(evidence, decision=ScriptedDecision("skip_review", value=value), reviewer=reviewer)
    assert (report.outcome == "skipped") == skipped
    assert reviewer.depths == ([] if skipped else ["standard"])


def test_low_decision_confidence_goes_directly_to_deep_review(evidence):
    reviewer = ScriptedReview(result())
    report = run(evidence, decision=ScriptedDecision(value=0.4), reviewer=reviewer)
    assert report.outcome == "reviewed"
    assert report.route == "deep"
    assert reviewer.depths == ["deep"]


def test_explicit_human_decision_stops_pipeline(evidence):
    reviewer = ScriptedReview()
    report = run(evidence, decision=ScriptedDecision("needs_human_review"), reviewer=reviewer)
    assert report.route == "human"
    assert reviewer.depths == []


@pytest.mark.parametrize("first", [result("uncertain"), result(value=0.849)])
def test_uncertainty_or_low_confidence_escalates_to_deep(evidence, first):
    reviewer = ScriptedReview(first, result())
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.route == "deep"
    assert report.outcome == "reviewed"
    assert reviewer.depths == ["standard", "deep"]


def test_review_threshold_is_inclusive(evidence):
    reviewer = ScriptedReview(result(value=0.85))
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.route == "standard"


def test_unavailable_confidence_cannot_clear_even_at_zero_thresholds(evidence):
    reviewer = ScriptedReview(result(value=0.0, source="unavailable"))
    report = run(
        evidence,
        policy=Policy(skip_confidence=0.0, review_confidence=0.0),
        decision=ScriptedDecision("skip_review", value=0.0, source="unavailable"),
        reviewer=reviewer,
    )
    assert report.outcome == "needs_human_review"
    assert reviewer.depths == ["deep"]


def test_concerns_survive_later_no_concerns_response(evidence):
    reviewer = ScriptedReview(result("concerns", findings=[finding()]), result())
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.outcome == "needs_human_review"
    assert reviewer.depths == ["standard", "deep"]
    assert report.findings == [finding()]


def test_concern_without_located_findings_still_escalates(evidence):
    reviewer = ScriptedReview(result("concerns"), result())
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.route == "human"


def test_findings_are_deduplicated_and_capped(evidence):
    items = [finding(title=f"Concern {i}") for i in range(8)]
    reviewer = ScriptedReview(
        result("concerns", findings=items), result("concerns", findings=items)
    )
    report = run(
        evidence, policy=Policy(max_findings=2), decision=ScriptedDecision(), reviewer=reviewer
    )
    assert report.outcome == "needs_human_review"
    assert len(report.findings) == 2
    assert report.findings_omitted == 6
    assert all(len(stage.result.findings) == 2 for stage in report.reviews)


def test_finding_on_unknown_file_fails_closed(evidence):
    reviewer = ScriptedReview(result("concerns", findings=[finding(path="unknown.py")]))
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.route == "human"
    assert report.reviews == []


@pytest.mark.parametrize("stage", ["decision", "standard", "deep"])
def test_provider_errors_do_not_leak_private_details(evidence, stage):
    class BrokenDecision:
        def decide(self, evidence):
            raise RuntimeError("PRIVATE_CREDENTIAL")

    class BrokenReview:
        def review(self, evidence, *, depth):
            if depth == stage:
                raise RuntimeError("PRIVATE_CREDENTIAL")
            return result("uncertain")

    report = run(
        evidence,
        decision=BrokenDecision() if stage == "decision" else ScriptedDecision(),
        reviewer=BrokenReview(),
    )
    assert report.outcome == "needs_human_review"
    assert "PRIVATE_CREDENTIAL" not in report.model_dump_json()
    assert any("failed" in reason for reason in report.reasons)


def test_malformed_provider_response_requires_human(evidence):
    class InvalidDecision:
        def decide(self, evidence):
            return {"recommendation": "skip_review", "confidence": 1.0}

    report = run(evidence, decision=InvalidDecision())
    assert report.outcome == "needs_human_review"


@pytest.mark.parametrize(
    ("before", "after", "path"),
    [
        ("return the the", "return the", "source.py"),
        ("`the the`", "`the`", "docs/usage.md"),
        ("the value is 1", "the value is 2", "docs/usage.md"),
    ],
)
def test_mocks_do_not_skip_source_or_substantive_documentation(evidence, before, after, path):
    evidence.files[0].path = path
    evidence.files[0].patch = f"@@ -1 +1 @@\n-{before}\n+{after}\n"
    report = run(evidence)
    assert report.outcome == "needs_human_review"
    assert [stage.depth for stage in report.reviews] == ["standard", "deep"]


@pytest.mark.parametrize(
    "patch",
    [
        ("@@ -1,4 +1,4 @@\n A command example:\n ```sh\n-echo go go\n+echo go\n ```\n"),
        (
            "@@ -1,3 +1,3 @@\n"
            " These commands are examples.\n"
            "-    echo go go\n"
            "+    echo go\n"
            " Run the next command afterward.\n"
        ),
    ],
)
def test_mocks_do_not_treat_code_as_editorial(evidence, patch):
    evidence.files[0].patch = patch
    report = run(evidence)
    assert report.outcome == "needs_human_review"
    assert [stage.depth for stage in report.reviews] == ["standard", "deep"]


def test_mocks_remain_uncertain_without_prose_context(evidence):
    evidence.files[0].patch = "@@ -1 +1 @@\n-echo go go\n+echo go\n"
    report = run(evidence)
    assert report.outcome == "needs_human_review"


def test_mock_concern_only_uses_added_lines(evidence):
    evidence.files[0].patch = "@@ -1 +1 @@\n-MOCK_REVIEW_CONCERN\n+Updated guide.\n"
    report = run(evidence)
    assert report.findings == []
    evidence.files[0].patch = "@@ -1 +1 @@\n-Old guide.\n+MOCK_REVIEW_CONCERN\n"
    report = run(evidence)
    assert report.outcome == "needs_human_review"
    assert len(report.findings) == 1
    assert report.findings[0].line == 1


@pytest.mark.parametrize(
    "confidence",
    [
        {"value": float("nan"), "source": "mock"},
        {"value": 1.1, "source": "mock"},
        {"value": 0.9, "source": "calibrated"},
        {"value": 0.9, "source": "unavailable"},
    ],
)
def test_invalid_confidence_is_rejected(confidence):
    with pytest.raises(ValidationError):
        Confidence.model_validate(confidence)


def test_calibrated_confidence_records_provenance():
    confidence = Confidence(value=0.9, source="calibrated", calibration_id="evaluation-1")
    assert confidence.calibration_id == "evaluation-1"


GOOD_REVIEW = {
    "outcome": "no_concerns",
    "confidence": {"value": 0.99, "source": "mock"},
    "summary": "Raw JSON review.",
    "provider": "raw-review",
    "findings": [],
}


class RawReview:
    """Returns scripted raw payloads; records whether strict mode was requested."""

    def __init__(self, *payloads):
        self.payloads = iter(payloads)
        self.strict = []

    def review(self, evidence, *, depth, strict_schema=False):
        self.strict.append(strict_schema)
        return next(self.payloads)


def test_valid_raw_json_payload_maps_to_contract(evidence):
    reviewer = RawReview(json.dumps(GOOD_REVIEW))
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.route == "standard"
    assert report.reviews[0].result.provider == "raw-review"
    assert reviewer.strict == [False]


@pytest.mark.parametrize(
    "mutation",
    [
        {"outcome": "maybe"},
        {"confidence": {"value": 1.5, "source": "mock"}},
        {"confidence": {"value": "0.9", "source": "mock"}},
        {"summary": None},
    ],
)
def test_invalid_payload_retries_once_then_requires_human(evidence, mutation):
    bad = json.dumps({**GOOD_REVIEW, **mutation})
    reviewer = RawReview(bad, bad)
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.outcome == "needs_human_review"
    assert reviewer.strict == [False, True]
    assert any("schema validation" in reason for reason in report.reasons)


def test_missing_field_and_non_json_fail(evidence):
    missing = {k: v for k, v in GOOD_REVIEW.items() if k != "provider"}
    reviewer = RawReview(missing, "not json")
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.outcome == "needs_human_review"


def test_retry_can_recover(evidence):
    reviewer = RawReview({**GOOD_REVIEW, "outcome": "bad"}, GOOD_REVIEW)
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.route == "standard"
    assert reviewer.strict == [False, True]


def test_retry_works_without_strict_mode_support(evidence):
    class Plain:
        def __init__(self):
            self.calls = 0

        def review(self, evidence, *, depth):
            self.calls += 1
            return {"outcome": "bad"} if self.calls == 1 else GOOD_REVIEW

    reviewer = Plain()
    report = run(evidence, decision=ScriptedDecision(), reviewer=reviewer)
    assert report.route == "standard"
    assert reviewer.calls == 2


def test_schema_diagnostics_exclude_input_values(evidence):
    class Leaky:
        def decide(self, evidence):
            return {"recommendation": "PRIVATE_VALUE", "confidence": 1.0}

    report = run(evidence, decision=Leaky())
    assert report.outcome == "needs_human_review"
    assert "PRIVATE_VALUE" not in report.model_dump_json()
    assert any("recommendation" in reason for reason in report.reasons)
