"""Validated evidence, provider responses, and advisory report contracts."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]


class Contract(BaseModel):
    """Base for strict, closed-schema data exchanged by the router."""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class ChangedFile(Contract):
    """One changed path and its exported diff metadata."""

    path: Text
    status: Literal["added", "modified", "removed", "renamed"]
    additions: Count
    deletions: Count
    patch: str | None = None
    patch_truncated: bool = False


class PullRequestEvidence(Contract):
    """Pull request text and file patches supplied to the review pipeline."""

    repository: Text
    number: Annotated[int, Field(gt=0)]
    base_sha: Text
    head_sha: Text
    title: Text
    body: str = ""
    files: list[ChangedFile]
    files_complete: bool = True

    @model_validator(mode="after")
    def unique_paths(self) -> Self:
        """Reject duplicate paths so patch coverage and finding checks stay unambiguous.

        :returns: This evidence after successful validation.
        :raises ValueError: If multiple changed-file entries have the same path.
        """
        paths = [file.path for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("changed file paths must be unique")
        return self


class Confidence(Contract):
    """A confidence value paired with provenance; unavailable confidence is zero."""

    value: Probability
    source: Literal["mock", "self_reported", "calibrated", "unavailable"]
    calibration_id: Text | None = None

    @model_validator(mode="after")
    def calibration_provenance(self) -> Self:
        """Require provenance for calibrated scores and constrain unavailable scores.

        :returns: This confidence value after successful validation.
        :raises ValueError: If calibrated or unavailable confidence is inconsistent.
        """
        if self.source == "calibrated" and self.calibration_id is None:
            raise ValueError("calibrated confidence requires a calibration_id")
        if self.source == "unavailable" and self.value != 0:
            raise ValueError("unavailable confidence must have value zero")
        return self


class Decision(Contract):
    """A provider's routing recommendation and its rationale."""

    recommendation: Literal["skip_review", "review", "needs_human_review", "accept", "reject"]
    confidence: Confidence
    reason: Text
    provider: Text


class Finding(Contract):
    """A review concern tied to a changed file and, optionally, a line."""

    path: Text
    line: Annotated[int, Field(gt=0)] | None = None
    severity: Literal["low", "medium", "high"]
    title: Text
    detail: Text


class ReviewResult(Contract):
    """One provider review result; findings are valid only for concerns."""

    outcome: Literal["no_concerns", "concerns", "uncertain"]
    confidence: Confidence
    summary: Text
    provider: Text
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def findings_match_outcome(self) -> Self:
        """Keep findings consistent with the declared review outcome.

        :returns: This review result after successful validation.
        :raises ValueError: If findings are present without a concerns outcome.
        """
        if self.findings and self.outcome != "concerns":
            raise ValueError("findings require the concerns outcome")
        return self


class ReviewStage(Contract):
    """The result of one review pass at a specified depth."""

    depth: Literal["standard", "deep"]
    result: ReviewResult


class Coverage(Contract):
    """Patch coverage summary and any reasons evidence is incomplete."""

    complete: bool
    files_total: Count
    files_with_valid_patches: Count
    evidence_bytes: Count
    issues: list[str] = Field(default_factory=list)


class PRTextValidation(Contract):
    """Outcome of optional deterministic pull request text checks."""

    enabled: bool
    valid: bool
    issues: list[str] = Field(default_factory=list)


class RubricCriterionResult(Contract):
    """Score and pass/blocker metadata for one configured rubric criterion."""

    criterion_id: Text
    score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    weight: Annotated[int, Field(gt=0)]
    blocker: Literal["hard", "soft"]
    pass_score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    passed: bool
    explanation: Text


class RubricEvaluation(Contract):
    """Aggregate rubric score, pass status, and criterion-level results."""

    enabled: bool
    score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    minimum_score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    passed: bool
    failed_hard_blockers: list[str] = Field(default_factory=list)
    failed_soft_criteria: list[str] = Field(default_factory=list)
    criteria: list[RubricCriterionResult] = Field(default_factory=list)


class ReviewReport(Contract):
    """Advisory-only routing outcome with evidence checks and review results."""

    schema_version: Literal["3"] = "3"
    advisory_only: Literal[True] = True
    repository: Text
    number: Annotated[int, Field(gt=0)]
    base_sha: Text
    head_sha: Text
    outcome: Literal[
        "skipped", "reviewed", "needs_human_review", "accepted", "rejected", "feedback"
    ]
    route: Literal["no_review", "standard", "deep", "human", "direct", "feedback", "blocked"]
    reasons: list[str]
    coverage: Coverage
    pr_text: PRTextValidation
    rubric: RubricEvaluation
    decision: Decision | None = None
    reviews: list[ReviewStage] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    findings_omitted: Count = 0
