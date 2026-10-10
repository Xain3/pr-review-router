"""Provider-independent advisory routing; incomplete evidence fails closed."""

import re
import subprocess
from typing import Literal

from .config import Policy, RubricCriterion
from .contracts import (
    Coverage,
    Finding,
    NonSemanticCheckResult,
    NonSemanticChecks,
    PRTextValidation,
    PullRequestEvidence,
    ReviewReport,
    ReviewStage,
    RubricCriterionResult,
    RubricEvaluation,
)
from .patches import parse_patch
from .providers import (
    DecisionProvider,
    ProviderSchemaError,
    ReviewProvider,
    request_decision,
    request_review,
)

_CONVENTIONAL_COMMIT_TITLE = re.compile(r"^[a-z][a-z0-9-]*(?:\([^\s()]+\))?!?: \S.*$")
_MARKDOWN_HEADING = re.compile(r"^ {0,3}#{1,6}\s+")
_FENCED_CODE_START = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def assess_coverage(evidence: PullRequestEvidence, policy: Policy) -> Coverage:
    """Check that every changed file has a complete, metadata-consistent patch.

    :param evidence: Pull request evidence and exported file patches to check.
    :param policy: Limits used to validate evidence size and file count.
    :returns: Patch coverage summary with any evidence issues.
    """
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


def _has_nonempty_body_section(body: str, section: str) -> bool:
    """Check for meaningful section content while ignoring Markdown headings.

    Fenced code counts as content, but headings inside a fence cannot start or
    end a section.

    :param body: Pull request body in Markdown.
    :param section: Heading text to find at level two.
    :returns: Whether the section contains non-heading content.
    """
    lines = []
    fence: tuple[str, int] | None = None
    for line in body.splitlines():
        if fence is not None:
            lines.append((line, True))
            marker, length = fence
            if re.match(rf"^ {{0,3}}{re.escape(marker)}{{{length},}}[ \t]*$", line):
                fence = None
            continue
        opening = _FENCED_CODE_START.match(line)
        if opening:
            marker = opening.group(1)
            fence = (marker[0], len(marker))
            lines.append((line, True))
        else:
            lines.append((line, False))

    section_heading = re.compile(rf"^ {{0,3}}##[ \t]+{re.escape(section)}[ \t]*$")
    for index, (line, in_fence) in enumerate(lines):
        if in_fence or not section_heading.fullmatch(line):
            continue
        for content, content_in_fence in lines[index + 1 :]:
            if not content_in_fence and re.match(r"^ {0,3}#{1,2}(?:[ \t]|$)", content):
                break
            if content.strip() and (content_in_fence or not _MARKDOWN_HEADING.match(content)):
                return True
    return False


def assess_pr_text(evidence: PullRequestEvidence, policy: Policy) -> PRTextValidation:
    """Apply configured title-format and required-body-section checks.

    :param evidence: Pull request title and body to validate.
    :param policy: Text-check settings and required section names.
    :returns: Validation status and any failed checks.
    """
    issues = []
    if policy.title_format == "conventional_commit" and not _CONVENTIONAL_COMMIT_TITLE.fullmatch(
        evidence.title
    ):
        issues.append("PR title must follow Conventional Commits format: type(scope): description.")
    for section in policy.required_body_sections:
        if not _has_nonempty_body_section(evidence.body, section):
            issues.append(f"PR body must include a non-empty '## {section}' section.")
    return PRTextValidation(
        enabled=policy.title_format != "any" or bool(policy.required_body_sections),
        valid=not issues,
        issues=issues,
    )


def _criterion_score(
    criterion: RubricCriterion, evidence: PullRequestEvidence
) -> tuple[float, str]:
    """Score one configured text check on the rubric's 0–100 scale.

    :param criterion: Rubric check and its configured comparison value.
    :param evidence: Pull request text evaluated by the check.
    :returns: Score from 0 to 100 and a human-readable explanation.
    """
    text = getattr(evidence, criterion.field)
    if criterion.check == "non_empty":
        passed = bool(text.strip())
        return (100.0 if passed else 0.0, "Text is present." if passed else "Text is empty.")
    if criterion.check == "contains":
        assert criterion.value is not None
        passed = criterion.value.casefold() in text.casefold()
        explanation = (
            f"Required phrase '{criterion.value}' was found."
            if passed
            else f"Required phrase '{criterion.value}' was not found."
        )
        return (100.0 if passed else 0.0, explanation)
    if criterion.check == "section_nonempty":
        assert criterion.value is not None
        passed = _has_nonempty_body_section(text, criterion.value)
        explanation = (
            f"Section '## {criterion.value}' has content."
            if passed
            else f"Section '## {criterion.value}' is missing or empty."
        )
        return (100.0 if passed else 0.0, explanation)

    assert criterion.minimum_words is not None
    word_count = len(re.findall(r"\b[\w'-]+\b", text))
    score = min(100.0, word_count / criterion.minimum_words * 100)
    return (
        score,
        f"Found {word_count} words; {criterion.minimum_words} required.",
    )


def assess_pr_text_rubric(evidence: PullRequestEvidence, policy: Policy) -> RubricEvaluation:
    """Score configured rubric criteria and determine whether routing may proceed.

    :param evidence: Pull request title and body to score.
    :param policy: Rubric criteria and minimum passing score.
    :returns: Aggregate score, criterion results, and pass status.
    """
    if not policy.rubric_criteria:
        return RubricEvaluation(
            enabled=False,
            minimum_score=policy.rubric_minimum_score,
            passed=True,
        )

    results = []
    for criterion in policy.rubric_criteria:
        score, explanation = _criterion_score(criterion, evidence)
        results.append(
            RubricCriterionResult(
                criterion_id=criterion.criterion_id,
                score=score,
                weight=criterion.weight,
                blocker=criterion.blocker,
                pass_score=criterion.pass_score,
                passed=score >= criterion.pass_score,
                explanation=explanation,
            )
        )
    score = sum(result.score * result.weight for result in results) / sum(
        result.weight for result in results
    )
    hard_failures = [
        result.criterion_id for result in results if result.blocker == "hard" and not result.passed
    ]
    soft_failures = [
        result.criterion_id for result in results if result.blocker == "soft" and not result.passed
    ]
    return RubricEvaluation(
        enabled=True,
        score=score,
        minimum_score=policy.rubric_minimum_score,
        passed=not hard_failures and score >= policy.rubric_minimum_score,
        failed_hard_blockers=hard_failures,
        failed_soft_criteria=soft_failures,
        criteria=results,
    )


def _check_target(evidence: PullRequestEvidence, target: str) -> str:
    """Build the text selected by a regex policy check.

    :param evidence: Validated pull request evidence.
    :param target: Configured evidence field to inspect.
    :returns: Text representation of the selected field.
    """
    if target == "title":
        return evidence.title
    if target == "body":
        return evidence.body
    if target == "patch":
        return "\n".join(file.patch or "" for file in evidence.files)
    return "\n".join(file.path for file in evidence.files)


def assess_nonsemantic_checks(evidence: PullRequestEvidence, policy: Policy) -> NonSemanticChecks:
    """Run configured regex and external-script policy checks.

    Regexes run against the configured evidence field. Scripts receive compact
    validated evidence JSON on stdin and must exit with status zero to pass.

    :param evidence: Validated pull request evidence to check.
    :param policy: Non-semantic checks and execution timeout.
    :returns: Individual check outcomes and the aggregate pass status.
    """
    results = []
    serialized = evidence.model_dump_json()
    for check in policy.nonsemantic_checks:
        if check.kind == "regex":
            assert check.target is not None and check.pattern is not None
            matched = re.search(check.pattern, _check_target(evidence, check.target)) is not None
            passed = matched == check.must_match
            expectation = "match" if check.must_match else "not match"
            detail = f"Regex {expectation} succeeded." if passed else f"Regex {expectation} failed."
        else:
            assert check.command is not None
            try:
                completed = subprocess.run(
                    check.command,
                    input=serialized,
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=policy.check_timeout_seconds,
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                passed = False
                detail = "External check timed out."
            except OSError:
                passed = False
                detail = "External check could not be started."
            else:
                passed = completed.returncode == 0
                detail = (
                    "External check passed."
                    if passed
                    else f"External check exited with status {completed.returncode}."
                )
        results.append(
            NonSemanticCheckResult(
                check_id=check.check_id,
                description=check.description,
                kind=check.kind,
                passed=passed,
                detail=detail,
            )
        )
    return NonSemanticChecks(
        enabled=bool(results),
        passed=all(result.passed for result in results),
        checks=results,
    )


def _findings(reviews: list[ReviewStage]) -> list[Finding]:
    """Collect findings once each, preserving their first-seen review order.

    :param reviews: Review stages whose findings should be combined.
    :returns: Unique findings in first-seen order.
    """
    unique: list[Finding] = []
    seen: set[tuple[str, int | None, str, str, str]] = set()
    for stage in reviews:
        for finding in stage.result.findings:
            key = (finding.path, finding.line, finding.severity, finding.title, finding.detail)
            if key not in seen:
                seen.add(key)
                unique.append(finding)
    return unique


def review_pull_request(
    evidence: PullRequestEvidence,
    policy: Policy,
    decision_provider: DecisionProvider,
    review_provider: ReviewProvider,
) -> ReviewReport:
    """Route validated evidence through decision and review providers.

    Incomplete evidence, failed checks, and provider errors fail closed. Review
    concerns are retained during escalation, and every result remains advisory.

    :param evidence: Validated pull request metadata and changed-file patches.
    :param policy: Routing thresholds and review behavior settings.
    :param decision_provider: Provider that recommends a routing decision.
    :param review_provider: Provider that assesses evidence at a requested depth.
    :returns: Advisory report containing routing, validation, and review results.
    """
    coverage = assess_coverage(evidence, policy)
    pr_text = assess_pr_text(evidence, policy)
    nonsemantic_checks = assess_nonsemantic_checks(evidence, policy)
    rubric = assess_pr_text_rubric(evidence, policy)
    decision = None
    reviews: list[ReviewStage] = []
    reasons = [*coverage.issues, *pr_text.issues]
    reasons.extend(
        f"Non-semantic check failed: {check.check_id} ({check.detail})"
        for check in nonsemantic_checks.checks
        if not check.passed
    )
    reasons.extend(
        f"Rubric hard blocker failed: {criterion_id}."
        for criterion_id in rubric.failed_hard_blockers
    )
    if rubric.enabled and rubric.score is not None and rubric.score < rubric.minimum_score:
        reasons.append(
            f"Rubric score {rubric.score:.1f} is below the {rubric.minimum_score:.1f} minimum."
        )

    def report(
        outcome: Literal[
            "skipped", "reviewed", "needs_human_review", "accepted", "rejected", "feedback"
        ],
        route: Literal["no_review", "standard", "deep", "human", "direct", "feedback", "blocked"],
    ) -> ReviewReport:
        """Build a report with both top-level and per-stage findings bounded.

        :param outcome: Final advisory outcome to include in the report.
        :param route: Routing path associated with the outcome.
        :returns: Report with exported findings limited by policy.
        """
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
            pr_text=pr_text,
            nonsemantic_checks=nonsemantic_checks,
            rubric=rubric,
            decision=decision,
            reviews=exported,
            findings=findings[: policy.max_findings],
            findings_omitted=max(0, len(findings) - policy.max_findings),
        )

    def unresolved() -> ReviewReport:
        """Apply the configured outcome when evidence or routing is unresolved.

        :returns: Rejection or human-review report, according to policy.
        """
        if policy.unresolved_outcome == "rejected":
            reasons.append("Policy blocks unresolved evidence or routing without a human handoff.")
            return report("rejected", "blocked")
        return report("needs_human_review", "human")

    if (
        not coverage.complete
        or not pr_text.valid
        or not nonsemantic_checks.passed
        or not rubric.passed
    ):
        return unresolved()
    try:
        decision = request_decision(decision_provider, evidence)
    except ProviderSchemaError as error:
        reasons.append(f"Decision provider failed schema validation: {error}")
        return unresolved()
    except Exception:
        # Never include provider exception text: it can contain credentials or PR content.
        reasons.append("Decision provider failed or returned an invalid response.")
        return unresolved()
    reasons.append(decision.reason)
    if decision.recommendation == "needs_human_review":
        return unresolved()
    accepted = False
    if decision.recommendation in {"accept", "reject"}:
        accepting = decision.recommendation == "accept"
        enabled = policy.allow_direct_acceptance if accepting else policy.allow_direct_rejection
        threshold = policy.acceptance_confidence if accepting else policy.rejection_confidence
        if (
            enabled
            and decision.confidence.source != "unavailable"
            and decision.confidence.value >= threshold
        ):
            if not accepting:
                return report("rejected", "direct")
            if not policy.acceptance_feedback:
                return report("accepted", "direct")
            accepted = True
        else:
            reasons.append(
                "Direct decision is disabled or does not meet its confidence threshold; continuing to review."
            )

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

    feedback = accepted or policy.review_behavior == "feedback"
    depths = ("standard", "deep")
    if not feedback and (
        decision.confidence.value < policy.review_confidence
        or decision.confidence.source == "unavailable"
    ):
        reasons.append("Decision uncertainty requires deep review.")
        depths = ("deep",)
    if feedback:
        depths = (policy.feedback_depth,)
    for depth in depths:
        try:
            result = request_review(review_provider, evidence, depth=depth)
            paths = {file.path for file in evidence.files}
            if any(finding.path not in paths for finding in result.findings):
                raise ValueError("finding references an unknown file")
        except ProviderSchemaError as error:
            reasons.append(f"{depth.capitalize()} review failed schema validation: {error}")
            return unresolved()
        except Exception:
            reasons.append(f"{depth.capitalize()} review failed or returned an invalid response.")
            return unresolved()
        reviews.append(ReviewStage(depth=depth, result=result))
        reasons.append(result.summary)
        if feedback:
            reasons.append("Reviewer feedback is non-blocking; no human handoff is required.")
            return report("accepted" if accepted else "feedback", "feedback")
        if (
            result.outcome == "no_concerns"
            and result.confidence.value >= policy.review_confidence
            and result.confidence.source != "unavailable"
            and not any(stage.result.outcome == "concerns" for stage in reviews)
        ):
            return report("reviewed", depth)
        reasons.append(f"{depth.capitalize()} review requires escalation.")
    return unresolved()
