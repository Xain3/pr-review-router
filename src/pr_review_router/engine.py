"""Provider-independent advisory routing; incomplete evidence fails closed."""

from .config import Policy
from .contracts import (
    Coverage,
    Decision,
    Finding,
    PullRequestEvidence,
    ReviewReport,
    ReviewResult,
    ReviewStage,
)
from .patches import parse_patch
from .providers import DecisionProvider, ReviewProvider


def assess_coverage(evidence: PullRequestEvidence, policy: Policy) -> Coverage:
    issues = []
    valid = 0
    size = len(evidence.model_dump_json().encode("utf-8"))
    if not evidence.files_complete:
        issues.append("The changed-file list is incomplete.")
    if not evidence.files:
        issues.append("No changed files were supplied.")
    if len(evidence.files) > policy.max_files:
        issues.append("The changed-file count exceeds the policy budget.")
    if size > policy.max_input_bytes:
        issues.append("The evidence exceeds the policy input budget.")
    for file in evidence.files:
        if file.patch_truncated:
            issues.append(f"{file.path}: patch is truncated.")
        elif not file.patch or not file.patch.strip():
            issues.append(f"{file.path}: patch is missing.")
        else:
            try:
                hunks = parse_patch(file.patch)
            except ValueError:
                issues.append(f"{file.path}: patch has invalid or incomplete hunks.")
                continue
            additions = sum(len(hunk.added) for hunk in hunks)
            deletions = sum(len(hunk.removed) for hunk in hunks)
            if (additions, deletions) != (file.additions, file.deletions):
                issues.append(f"{file.path}: patch counts disagree with file metadata.")
            elif additions + deletions == 0:
                issues.append(f"{file.path}: no textual changes are available.")
            else:
                valid += 1
    return Coverage(
        complete=not issues,
        files_total=len(evidence.files),
        files_with_valid_patches=valid,
        evidence_bytes=size,
        issues=issues,
    )


def _findings(reviews: list[ReviewStage]) -> list[Finding]:
    unique: list[Finding] = []
    for stage in reviews:
        for finding in stage.result.findings:
            if finding not in unique:
                unique.append(finding)
    return unique


def review_pull_request(
    evidence: PullRequestEvidence,
    policy: Policy,
    decision_provider: DecisionProvider,
    review_provider: ReviewProvider,
) -> ReviewReport:
    coverage = assess_coverage(evidence, policy)
    decision = None
    reviews: list[ReviewStage] = []
    reasons = list(coverage.issues)

    def report(outcome: str, route: str) -> ReviewReport:
        findings = _findings(reviews)
        # Cap both the summary and nested stage findings in the exported report.
        exported = [
            stage.model_copy(
                update={
                    "result": stage.result.model_copy(
                        update={"findings": stage.result.findings[: policy.max_findings]}
                    )
                }
            )
            for stage in reviews
        ]
        return ReviewReport(
            repository=evidence.repository,
            number=evidence.number,
            base_sha=evidence.base_sha,
            head_sha=evidence.head_sha,
            outcome=outcome,
            route=route,
            reasons=reasons,
            coverage=coverage,
            decision=decision,
            reviews=exported,
            findings=findings[: policy.max_findings],
            findings_omitted=max(0, len(findings) - policy.max_findings),
        )

    if not coverage.complete:
        return report("needs_human_review", "human")
    try:
        decision = Decision.model_validate(decision_provider.decide(evidence))
    except Exception:
        # Never include provider exception text: it can contain credentials or PR content.
        reasons.append("Decision provider failed or returned an invalid response.")
        return report("needs_human_review", "human")
    reasons.append(decision.reason)
    if decision.recommendation == "needs_human_review":
        return report("needs_human_review", "human")
    if (
        decision.recommendation == "skip_review"
        and decision.confidence.value >= policy.skip_confidence
        and decision.confidence.source != "unavailable"
    ):
        return report("skipped", "no_review")
    if (
        decision.recommendation == "skip_review"
        and decision.confidence.value < policy.skip_confidence
    ):
        reasons.append("Decision confidence does not meet the skip threshold.")

    depths = ("standard", "deep")
    if (
        decision.confidence.value < policy.review_confidence
        or decision.confidence.source == "unavailable"
    ):
        reasons.append("Decision uncertainty requires deep review.")
        depths = ("deep",)
    for depth in depths:
        try:
            result = ReviewResult.model_validate(review_provider.review(evidence, depth=depth))
            paths = {file.path for file in evidence.files}
            if any(finding.path not in paths for finding in result.findings):
                raise ValueError("finding references an unknown file")
        except Exception:
            reasons.append(f"{depth.capitalize()} review failed or returned an invalid response.")
            return report("needs_human_review", "human")
        reviews.append(ReviewStage(depth=depth, result=result))
        reasons.append(result.summary)
        if (
            result.outcome == "no_concerns"
            and result.confidence.value >= policy.review_confidence
            and result.confidence.source != "unavailable"
            and not any(stage.result.outcome == "concerns" for stage in reviews)
        ):
            return report("reviewed", depth)
        reasons.append(f"{depth.capitalize()} review requires escalation.")
    return report("needs_human_review", "human")
