"""Provider protocols and deliberately limited, deterministic offline mocks."""

import re
from pathlib import PurePosixPath
from typing import Literal, Protocol

from .contracts import Confidence, Decision, Finding, PullRequestEvidence, ReviewResult
from .patches import parse_patch

Depth = Literal["standard", "deep"]
_FENCE = re.compile(r"^(?:> ?)* {0,3}(`{3,}|~{3,})")
_INDENTED_CODE = re.compile(r"^(?:> ?)*(?: {4,}|\t)")


class DecisionProvider(Protocol):
    def decide(self, evidence: PullRequestEvidence) -> Decision: ...


class ReviewProvider(Protocol):
    def review(self, evidence: PullRequestEvidence, *, depth: Depth) -> ReviewResult: ...


def _editorial(evidence: PullRequestEvidence) -> bool:
    """Recognize only duplicate-word corrections in plain prose documentation."""
    if not evidence.files:
        return False
    for file in evidence.files:
        if (
            file.status != "modified"
            or PurePosixPath(file.path).suffix.lower() not in {".md", ".txt"}
            or file.patch is None
            or file.additions == 0
            or file.additions != file.deletions
        ):
            return False
        for hunk in parse_patch(file.patch):
            if hunk.old_start != 1:
                return False
            if len(hunk.removed) != len(hunk.added):
                return False
            changed = False
            context_before = False
            context_after = False
            fence: tuple[str, int] | None = None
            for before, (_, after) in zip(hunk.removed, hunk.added, strict=True):
                if any(char in before + after for char in "`{}[]<>()#*_\t"):
                    return False
                corrected = re.sub(r"\b([A-Za-z]+) \1\b", r"\1", before)
                if corrected == before or corrected != after:
                    return False
            for prefix, content in hunk.lines:
                if prefix == " ":
                    marker = _FENCE.match(content)
                    if marker:
                        delimiter = marker[1]
                        if fence is None:
                            fence = (delimiter[0], len(delimiter))
                        elif (
                            delimiter[0] == fence[0]
                            and len(delimiter) >= fence[1]
                            and not content[marker.end() :].strip()
                        ):
                            fence = None
                    elif fence is None and not _INDENTED_CODE.match(content) and content.strip():
                        if not changed:
                            context_before = True
                        else:
                            context_after = True
                    continue
                if fence is not None or _INDENTED_CODE.match(content):
                    return False
                changed = True
            if not context_before or not context_after:
                return False
    return True


class MockDecisionProvider:
    def decide(self, evidence: PullRequestEvidence) -> Decision:
        if _editorial(evidence):
            return Decision(
                recommendation="skip_review",
                confidence=Confidence(value=0.99, source="mock"),
                reason="Mock recognizes a duplicate-word correction in documentation.",
                provider="mock-decision",
            )
        return Decision(
            recommendation="review",
            confidence=Confidence(value=0.9, source="mock"),
            reason="Mock routes all other changes to review.",
            provider="mock-decision",
        )


class MockReviewProvider:
    def review(self, evidence: PullRequestEvidence, *, depth: Depth) -> ReviewResult:
        findings = []
        for file in evidence.files:
            for hunk in parse_patch(file.patch or ""):
                for line, content in hunk.added:
                    if "MOCK_REVIEW_CONCERN" in content:
                        findings.append(
                            Finding(
                                path=file.path,
                                line=line,
                                severity="medium",
                                title="Synthetic mock concern",
                                detail="Added text contains the explicit MOCK_REVIEW_CONCERN marker.",
                            )
                        )
        if findings:
            return ReviewResult(
                outcome="concerns",
                confidence=Confidence(value=0.99, source="mock"),
                summary="Mock fixture markers request escalation.",
                provider=f"mock-{depth}",
                findings=findings,
            )
        if _editorial(evidence):
            return ReviewResult(
                outcome="no_concerns",
                confidence=Confidence(value=0.99, source="mock"),
                summary="Mock recognizes only an editorial duplicate-word correction.",
                provider=f"mock-{depth}",
            )
        return ReviewResult(
            outcome="uncertain",
            confidence=Confidence(value=0.5, source="mock"),
            summary="The mock cannot assess substantive changes; human review is required.",
            provider=f"mock-{depth}",
        )
