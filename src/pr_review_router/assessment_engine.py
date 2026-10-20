"""Authoritative communication assessments without review or reporter generation."""

from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from .assessment_rubric import (
    AssessmentRubric,
    CriterionAssessment,
    CriterionBatch,
    EnforcementRequirement,
    RubricDecisionProvider,
    Status,
    Suggestion,
    _requirement_members,
)
from .config import Policy
from .contracts import (
    Contract,
    Coverage,
    NonSemanticChecks,
    PRTextValidation,
    PullRequestEvidence,
    RubricEvaluation,
    Text,
)
from .engine import (
    assess_coverage,
    assess_nonsemantic_checks,
    assess_pr_text,
    assess_pr_text_rubric,
)
from .providers import _Call, _enforce


class AssessmentProvider(Protocol):
    """Criterion assessment boundary independent of legacy review routing."""

    def assess(self, evidence: PullRequestEvidence) -> CriterionBatch | dict[str, Any]:
        """Assess the configured rubric.

        :param evidence: Validated PR evidence.
        :returns: Complete criterion assessments and effective choice labels.
        """
        ...


class GateResult(Contract):
    """A required condition, including alternative criterion groups."""

    gate_id: Text
    status: Status
    criteria: list[str] = Field(default_factory=list)


class AssessmentReport(Contract):
    """Final communication verdict, explicitly separate from enforcement authority."""

    schema_version: Literal["1"] = "1"
    report_type: Literal["pr_communication"] = "pr_communication"
    advisory_only: Literal[True] = True
    automation_authorized: Literal[False] = False
    repository: Text
    number: int = Field(gt=0)
    base_sha: Text
    head_sha: Text
    verdict: Literal["accepted", "rejected", "unresolved"]
    coverage: Coverage
    pr_text: PRTextValidation
    nonsemantic_checks: NonSemanticChecks
    policy_rubric: RubricEvaluation
    criteria: list[CriterionAssessment]
    gates: list[GateResult] = Field(min_length=1)
    selected_enforcement_rule: int | None = Field(default=None, ge=0)
    blockers: list[str]
    suggestions: list[Suggestion]
    unresolved: list[str]
    diagnostics: list[str]
    provider: Text

    @model_validator(mode="after")
    def consistent_verdict(self) -> "AssessmentReport":
        """Keep the exported verdict consistent with every applicable gate.

        :returns: A report with matching verdict and gate-reason lists.
        :raises ValueError: If a verdict or reason list contradicts the gates.
        """
        blockers = [g.gate_id for g in self.gates if g.status == "failed"]
        unresolved = [g.gate_id for g in self.gates if g.status == "uncertain"]
        expected = "rejected" if blockers else "unresolved" if unresolved else "accepted"
        if self.verdict != expected or self.blockers != blockers or self.unresolved != unresolved:
            raise ValueError("assessment verdict must match its gate results")
        return self


def select_requirements(
    rubric: AssessmentRubric, batch: CriterionBatch
) -> tuple[list[EnforcementRequirement], list[EnforcementRequirement], int | None, bool]:
    """Resolve ordered enforcement without assuming uncertain conditions are false.

    :param rubric: Explicit criterion actions or ordered enforcement rules.
    :param batch: Assessments and confidence-adjusted choice labels.
    :returns: Blocking requirements, advisory requirements, rule index, and resolution.
    """
    if not rubric.enforcement_rules:
        return (
            [c.criterion_id for c in rubric.criteria if c.action == "block"],
            [c.criterion_id for c in rubric.criteria if c.action == "suggest"],
            None,
            True,
        )
    criteria = {c.criterion_id: c for c in rubric.criteria}
    for index, rule in enumerate(rubric.enforcement_rules):
        unknown = False
        impossible = False
        for identifier, accepted in rule.when.items():
            label = batch.labels.get(identifier)
            if label in accepted:
                continue
            if label is None or criteria[identifier].choice_statuses.get(label) == "uncertain":
                unknown = True
            else:
                impossible = True
        if impossible:
            continue
        if unknown:
            break
        return list(rule.enforce), list(rule.soft_enforce), index, True
    return [], [], None, False


def requirement_status(requirement: EnforcementRequirement, results: dict[str, Status]) -> Status:
    """Evaluate a single requirement or an any-of alternative group.

    :param requirement: Criterion ID or alternative group.
    :param results: Criterion statuses keyed by ID.
    :returns: Passed if any alternative passes, otherwise uncertain or failed.
    """
    statuses = [results[identifier] for identifier in _requirement_members(requirement)]
    return (
        "passed" if "passed" in statuses else "uncertain" if "uncertain" in statuses else "failed"
    )


def assess_pull_request(
    evidence: PullRequestEvidence,
    policy: Policy,
    rubric: AssessmentRubric,
    assessment_provider: AssessmentProvider | None = None,
) -> AssessmentReport:
    """Finalize communication gates without invoking a reviewer or reporter.

    :param evidence: Supplied PR title, description, and complete patches.
    :param policy: Input-integrity limits and deterministic policy gates.
    :param rubric: Nonempty communication rubric without supplied-review criteria.
    :param assessment_provider: Criterion provider; omission leaves semantics unresolved.
    :returns: Complete assessment report, including early failures and uncertainty.
    :raises ValueError: If a rubric requests legacy supplied-review assessment.
    """
    formal = RubricDecisionProvider(None, rubric, "", communication=True)
    coverage = assess_coverage(evidence, policy)
    pr_text = assess_pr_text(evidence, policy)
    checks = assess_nonsemantic_checks(evidence, policy)
    weighted = assess_pr_text_rubric(evidence, policy)
    gates = [
        GateResult(
            gate_id="preflight:coverage", status="passed" if coverage.complete else "uncertain"
        )
    ]
    if pr_text.enabled:
        gates.append(
            GateResult(gate_id="policy:pr_text", status="passed" if pr_text.valid else "failed")
        )
    if weighted.enabled:
        gates.append(
            GateResult(gate_id="policy:rubric", status="passed" if weighted.passed else "failed")
        )
    for check in checks.checks:
        # These diagnostics are engine-owned, never untrusted script/provider output.
        unavailable = "timed out" in check.detail or "could not" in check.detail
        gates.append(
            GateResult(
                gate_id=f"check:{check.check_id}",
                status=("passed" if check.passed else "uncertain" if unavailable else "failed"),
            )
        )
    batch = formal.assess(evidence)
    owned_formal = {c.criterion_id: c for c in batch.criteria if c.source == "deterministic"}
    formal_blocked = not rubric.enforcement_rules and any(
        c.action == "block"
        and c.criterion_id in owned_formal
        and owned_formal[c.criterion_id].status == "failed"
        for c in rubric.criteria
    )
    diagnostics = [*coverage.issues, *pr_text.issues]
    diagnostics.extend(c.detail for c in checks.checks if not c.passed)
    if formal_blocked or any(g.status != "passed" for g in gates):
        reason = (
            "Not assessed: an unconditional formal gate failed."
            if formal_blocked
            else "Not assessed: preflight gates prevented semantic inference."
        )
        batch.criteria = [
            c.model_copy(update={"explanation": reason}) if c.source == "unavailable" else c
            for c in batch.criteria
        ]
    if (
        all(g.status == "passed" for g in gates)
        and not formal_blocked
        and assessment_provider is not None
    ):
        try:
            candidate = _enforce(
                CriterionBatch, "Assessment", _Call(assessment_provider.assess, evidence)
            )
            if [c.criterion_id for c in candidate.criteria] != [
                c.criterion_id for c in rubric.criteria
            ]:
                raise ValueError("assessment criterion IDs differ from the rubric")
            for identifier, label in candidate.labels.items():
                criterion = next(c for c in rubric.criteria if c.criterion_id == identifier)
                if criterion.check != "semantic" or label not in criterion.choice_options:
                    raise ValueError("assessment choice labels differ from the rubric")
            candidate.criteria = [owned_formal.get(c.criterion_id, c) for c in candidate.criteria]
            if any(
                c.source == "unavailable" and c.status != "uncertain" for c in candidate.criteria
            ):
                raise ValueError("unavailable assessments must remain uncertain")
            batch = candidate
        except Exception:
            # Formal results survive inference failures; exception text may contain secrets.
            diagnostics.append("Criterion assessment failed or returned an invalid response.")
            batch.criteria = [
                c.model_copy(update={"explanation": "Not assessed: criterion provider failed."})
                if c.source == "unavailable"
                else c
                for c in batch.criteria
            ]
    blocking, advisory, selected, resolved = select_requirements(rubric, batch)
    if not resolved:
        gates.append(GateResult(gate_id="rules:selection", status="uncertain"))
    statuses: dict[str, Status] = {c.criterion_id: c.status for c in batch.criteria}
    for index, requirement in enumerate(blocking):
        members = _requirement_members(requirement)
        gates.append(
            GateResult(
                gate_id=f"rubric:{index}",
                criteria=members,
                status=requirement_status(requirement, statuses),
            )
        )
    definitions = {c.criterion_id: c for c in rubric.criteria}
    suggestions = [
        Suggestion(criterion_id=identifier, message=definitions[identifier].suggestion)
        for requirement in advisory
        if requirement_status(requirement, statuses) == "failed"
        for identifier in _requirement_members(requirement)
    ]
    suggestions.extend(
        Suggestion(criterion_id=f"policy:{c.criterion_id}", message=c.explanation)
        for c in weighted.criteria
        if not c.passed and c.blocker == "soft"
    )
    blockers = [g.gate_id for g in gates if g.status == "failed"]
    unresolved = [g.gate_id for g in gates if g.status == "uncertain"]
    verdict = "rejected" if blockers else "unresolved" if unresolved else "accepted"
    return AssessmentReport(
        repository=evidence.repository,
        number=evidence.number,
        base_sha=evidence.base_sha,
        head_sha=evidence.head_sha,
        verdict=verdict,
        coverage=coverage,
        pr_text=pr_text,
        nonsemantic_checks=checks,
        policy_rubric=weighted,
        criteria=batch.criteria,
        gates=gates,
        selected_enforcement_rule=selected,
        blockers=blockers,
        suggestions=suggestions,
        unresolved=unresolved,
        diagnostics=diagnostics,
        provider=batch.provider,
    )
