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


DecisionPayload = Decision | dict[str, Any] | str | bytes | bytearray
ReviewPayload = ReviewResult | dict[str, Any] | str | bytes | bytearray


class DecisionProvider(Protocol):
    """Interface for a provider that recommends a routing decision."""

    def decide(self, evidence: PullRequestEvidence) -> DecisionPayload:
        """Recommend how the pull request should be routed.

        :param evidence: Validated pull request metadata and file patches.
        :returns: Decision model or a payload accepted by the decision validator.
        """
        ...


class ReviewProvider(Protocol):
    """Interface for a provider that reviews evidence at a requested depth."""

    def review(self, evidence: PullRequestEvidence, *, depth: Depth) -> ReviewPayload:
        """Review pull request evidence at the requested depth.

        :param evidence: Validated pull request metadata and file patches.
        :param depth: Review depth requested by the routing engine.
        :returns: Review model or a payload accepted by the review validator.
        """
        ...


class ProviderSchemaError(ValueError):
    """Provider output violated the schema after the bounded retry.

    The message lists only field locations and error types, never input values,
    so it is safe to include in reports.
    """


def _diagnostic(error: ValidationError) -> str:
    """Format safe schema diagnostics without exposing untrusted input values.

    :param error: Pydantic validation error to summarize.
    :returns: Bounded diagnostic text containing field locations and error types.
    """

    def mask(part: object) -> str:
        """Redact unsafe validation-location components.

        :param part: Validation path component to render safely.
        :returns: Safe string representation or a redaction marker.
        """
        if isinstance(part, int):
            return str(part)
        if isinstance(part, str):
            if re.fullmatch(r"[a-z_][a-z0-9_]*", part):
                return part
            return "<redacted>"
        return "<redacted>"

    return "; ".join(
        f"{'.'.join(mask(part) for part in item['loc']) or '<root>'}: {item['type']}"
        for item in error.errors(include_input=False, include_url=False)[:5]
    )


def _validate[Model: BaseModel](model: type[Model], raw: Any) -> Model:
    """Validate a provider payload supplied as JSON text or a Python value.

    :param model: Pydantic model class used to validate the payload.
    :param raw: Provider payload as JSON-compatible text or a Python value.
    :returns: Validated instance of ``model``.
    :raises pydantic.ValidationError: If the payload does not match the model.
    """
    if isinstance(raw, str | bytes | bytearray):
        return model.model_validate_json(raw)
    return model.model_validate(raw)


def _enforce[Model: BaseModel](model: type[Model], label: str, call: Callable[..., Any]) -> Model:
    """Validate provider output, retrying once with strict-schema mode when supported.

    :param model: Pydantic model class used to validate provider output.
    :param label: Provider response name used in safe diagnostics.
    :param call: Callable that requests a provider response.
    :returns: Validated instance of ``model``.
    :raises ProviderSchemaError: If both provider responses fail validation.
    """
    raw = call()
    try:
        return _validate(model, raw)
    except ValidationError:
        pass
    try:
        supports = "strict_schema" in inspect.signature(call.func).parameters  # type: ignore[attr-defined]
    except (TypeError, ValueError, AttributeError):
        supports = False
    raw = call(strict_schema=True) if supports else call()
    try:
        return _validate(model, raw)
    except ValidationError as error:
        raise ProviderSchemaError(
            f"{label} response failed schema validation after retry ({_diagnostic(error)})"
        ) from None


def request_decision(provider: DecisionProvider, evidence: PullRequestEvidence) -> Decision:
    """Validate decision output at the provider boundary.

    Raises ``ProviderSchemaError`` when the response remains invalid after retry.

    :param provider: Provider that supplies the routing recommendation.
    :param evidence: Validated pull request evidence passed to the provider.
    :returns: Validated routing decision.
    :raises ProviderSchemaError: If the provider returns invalid output after retry.
    """
    return _enforce(Decision, "Decision", _Call(provider.decide, evidence))


def request_review(
    provider: ReviewProvider, evidence: PullRequestEvidence, *, depth: Depth
) -> ReviewResult:
    """Validate review output at the provider boundary.

    Raises ``ProviderSchemaError`` when the response remains invalid after retry.

    :param provider: Provider that reviews the pull request evidence.
    :param evidence: Validated pull request evidence passed to the provider.
    :param depth: Review depth requested from the provider.
    :returns: Validated review result.
    :raises ProviderSchemaError: If the provider returns invalid output after retry.
    """
    return _enforce(ReviewResult, "Review", _Call(provider.review, evidence, depth=depth))


class _Call:
    """Bind provider arguments while allowing retry-only keyword arguments."""

    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Bind arguments for an initial call and any retry.

        :param func: Provider method to invoke.
        :param args: Positional arguments bound to the method.
        :param kwargs: Keyword arguments bound to the method.
        """
        self.func, self.args, self.kwargs = func, args, kwargs

    def __call__(self, **extra: Any) -> Any:
        """Invoke the bound method with optional additional keyword arguments.

        :param extra: Keyword arguments added for this invocation.
        :returns: The raw result returned by the provider method.
        """
        return self.func(*self.args, **self.kwargs, **extra)


def _editorial(evidence: PullRequestEvidence) -> bool:
    """Recognize only duplicate-word corrections in plain prose documentation.

    :param evidence: Pull request evidence whose patches should be inspected.
    :returns: Whether every changed patch is a supported editorial correction.
    """
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
    """Deterministic offline decision provider for examples and smoke tests."""

    def decide(self, evidence: PullRequestEvidence) -> Decision:
        """Return a deterministic decision for an offline fixture.

        :param evidence: Validated pull request metadata and file patches.
        :returns: Synthetic decision based on fixture markers or patch contents.
        """
        markers = {
            marker
            for file in evidence.files
            for hunk in parse_patch(file.patch or "")
            for _, content in hunk.added
            for marker in ("MOCK_DECISION_REJECT", "MOCK_DECISION_ACCEPT")
            if marker in content
        }
        # Rejection wins if both synthetic markers are present.
        if markers:
            rejecting = "MOCK_DECISION_REJECT" in markers
            return Decision(
                recommendation="reject" if rejecting else "accept",
                confidence=Confidence(value=0.99, source="mock"),
                reason="Explicit synthetic marker requests a direct mock decision.",
                provider="mock-decision",
            )
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
    """Deterministic offline reviewer that recognizes explicit fixture markers."""

    def review(self, evidence: PullRequestEvidence, *, depth: Depth) -> ReviewResult:
        """Return a deterministic review result for an offline fixture.

        :param evidence: Validated pull request metadata and file patches.
        :param depth: Review depth recorded in the synthetic provider name.
        :returns: Synthetic review result based on fixture markers.
        """
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
