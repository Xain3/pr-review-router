"""Provider protocols and deliberately limited, deterministic offline mocks."""

import inspect
import re
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from .contracts import Confidence, Decision, Finding, PullRequestEvidence, ReviewResult
from .patches import parse_patch

Depth = Literal["standard", "deep"]
_FENCE = re.compile(r"^(?:> ?)* {0,3}(`{3,}|~{3,})")
_INDENTED_CODE = re.compile(r"^(?:> ?)*(?: {4,}|\t)")


class DecisionProvider(Protocol):
    def decide(self, evidence: PullRequestEvidence) -> Decision: ...


class ReviewProvider(Protocol):
    def review(self, evidence: PullRequestEvidence, *, depth: Depth) -> ReviewResult: ...


class ProviderSchemaError(ValueError):
    """Provider output violated the schema after the bounded retry.

    The message lists only field locations and error types, never input values,
    so it is safe to include in reports.
    """


def _diagnostic(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['type']}"
        for item in error.errors(include_input=False, include_url=False)[:5]
    )


def _validate[Model: BaseModel](model: type[Model], raw: Any) -> Model:
    if isinstance(raw, str | bytes | bytearray):
        return model.model_validate_json(raw)
    return model.model_validate(raw)


def _enforce[Model: BaseModel](model: type[Model], label: str, call: Callable[..., Any]) -> Model:
    """Validate a provider response; retry once in strict-schema mode if supported."""
    try:
        return _validate(model, call())
    except ValidationError:
        pass
    try:
        supports = "strict_schema" in inspect.signature(call.func).parameters  # type: ignore[attr-defined]
    except (TypeError, ValueError, AttributeError):
        supports = False
    try:
        return _validate(model, call(strict_schema=True) if supports else call())
    except ValidationError as error:
        raise ProviderSchemaError(
            f"{label} response failed schema validation after retry ({_diagnostic(error)})"
        ) from None


def request_decision(provider: DecisionProvider, evidence: PullRequestEvidence) -> Decision:
    """Single boundary for decision output; raises ProviderSchemaError when invalid."""
    return _enforce(Decision, "Decision", _Call(provider.decide, evidence))


def request_review(
    provider: ReviewProvider, evidence: PullRequestEvidence, *, depth: Depth
) -> ReviewResult:
    """Single boundary for review output; raises ProviderSchemaError when invalid."""
    return _enforce(ReviewResult, "Review", _Call(provider.review, evidence, depth=depth))


class _Call:
    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.func, self.args, self.kwargs = func, args, kwargs

    def __call__(self, **extra: Any) -> Any:
        return self.func(*self.args, **self.kwargs, **extra)


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
