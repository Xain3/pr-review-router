"""Shadow-mode experiments and labeled evaluation separate from the routing engine."""

import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field, field_validator, model_validator

from .assessment_rubric import (
    AssessmentRubric,
    Recommendation,
    RubricDecisionProvider,
    Status,
    load_assessment_rubric,
    rubric_versions,
)
from .config import Policy
from .contracts import Contract, Decision, PullRequestEvidence, ReviewReport, ReviewResult, Text
from .engine import review_pull_request
from .local_providers import (
    OllamaReviewProvider,
    OllayaDecisionProvider,
    ShadowDecisionProvider,
    adapter_versions,
    versions_fingerprint,
)
from .provider_config import DecisionSettings, ProvidersConfig, ReviewSettings
from .providers import MockDecisionProvider, MockReviewProvider
from .transport import Session, Tape, fingerprint


def _experiment_policy_inputs(policy: Policy) -> dict[str, Any]:
    """Return policy inputs while preserving replay hashes for older policies.

    :param policy: Routing policy used by the experiment.
    :returns: Policy fields that affect this experiment's provider interaction.
    """
    values = policy.model_dump(mode="json")
    if not policy.nonsemantic_checks:
        values.pop("nonsemantic_checks")
        values.pop("check_timeout_seconds")
    return values


def _experiment_report_schema(policy: Policy) -> dict[str, Any]:
    """Return the report schema relevant to an experiment fingerprint.

    :param policy: Routing policy used by the experiment.
    :returns: Current schema, or the pre-check schema for legacy policies.
    """
    schema = ReviewReport.model_json_schema()
    if not policy.nonsemantic_checks:
        schema["properties"].pop("nonsemantic_checks")
        schema["required"].remove("nonsemantic_checks")
        for name in ("NonSemanticCheckResult", "NonSemanticChecks"):
            schema["$defs"].pop(name, None)
    return schema


class _CapturingMockReviewProvider:
    """Retain complete mock responses before the engine applies report limits."""

    def __init__(self, provider: MockReviewProvider) -> None:
        """Wrap the mock reviewer and initialize its response capture.

        :param provider: Deterministic mock reviewer used by the engine.
        """
        self.provider = provider
        self.responses: list[dict[str, Any]] = []

    def review(
        self, evidence: PullRequestEvidence, *, depth: Literal["standard", "deep"]
    ) -> ReviewResult:
        """Capture one complete mock response and return it unchanged.

        :param evidence: Validated PR evidence.
        :param depth: Review depth requested by the engine.
        :returns: The mock review result.
        """
        result = self.provider.review(evidence, depth=depth)
        self.responses.append({"depth": depth, "result": result.model_dump(mode="json")})
        return result


class ExperimentResult:
    """Keep the unchanged advisory report separate from experiment-only artifacts."""

    def __init__(self, report: ReviewReport, artifact: dict[str, Any], tape: Tape | None) -> None:
        """Collect review and experiment outputs for the CLI or evaluation runner.

        :param report: Existing schema-version-3 advisory report.
        :param artifact: Model provenance, original decisions, and uncapped review attempts.
        :param tape: Explicitly requested live recording, if any.
        """
        self.report = report
        self.artifact = artifact
        self.tape = tape


def run_experiment(
    evidence: PullRequestEvidence,
    policy: Policy,
    config: ProvidersConfig,
    *,
    replay: Path | None = None,
    record: bool = False,
    http_transport: httpx.AsyncBaseTransport | None = None,
    rubric: AssessmentRubric | None = None,
    review_text: str = "",
) -> ExperimentResult:
    """Execute independent providers in shadow mode, preserving engine preflight gates.

    :param evidence: Validated PR evidence.
    :param policy: Existing routing policy with direct decisions disabled.
    :param config: Independent local or mock provider selections.
    :param replay: Optional exact offline HTTP recording.
    :param record: Whether to return a tape for explicit recording refresh.
    :param http_transport: Optional injectable transport for offline tests.
    :param rubric: Optional formal and semantic evidence rubric for the decision provider.
    :param review_text: Supplied review text assessed by an optional rubric.
    :returns: Advisory report plus separate experiment artifacts.
    :raises ValueError: If direct decisions are enabled or replay and recording are combined.
    """
    if policy.allow_direct_acceptance or policy.allow_direct_rejection:
        raise ValueError("local experiments require direct acceptance and rejection to be disabled")
    if replay and record:
        raise ValueError("recording and replay are mutually exclusive")
    if rubric and not isinstance(config.decision, DecisionSettings):
        raise ValueError("semantic rubric experiments require an Ollaya decision provider")
    fingerprint_inputs = {
        "experiment_version": 1,
        "providers": config.model_dump(mode="json"),
        "policy": _experiment_policy_inputs(policy),
        "evidence": evidence.model_dump(mode="json"),
        "adapters": adapter_versions(),
        "decision_schema": Decision.model_json_schema(),
        "review_schema": ReviewResult.model_json_schema(),
        "report_schema": _experiment_report_schema(policy),
    }
    if rubric:
        fingerprint_inputs["assessment_rubric"] = {
            "config": rubric.model_dump(mode="json"),
            "review_text": review_text,
            "versions": rubric_versions(),
        }
    experiment_fingerprint = fingerprint(fingerprint_inputs)
    session = Session(experiment_fingerprint, replay=replay, http_transport=http_transport)
    decision = (
        OllayaDecisionProvider(config.decision, session)
        if isinstance(config.decision, DecisionSettings)
        else MockDecisionProvider()
    )
    mock_reviewer = None
    if isinstance(config.review, ReviewSettings):
        reviewer = OllamaReviewProvider(config.review, session)
    else:
        mock_reviewer = _CapturingMockReviewProvider(MockReviewProvider())
        reviewer = mock_reviewer
    rubric_provider = (
        RubricDecisionProvider(
            decision, rubric, review_text, max_input_bytes=policy.max_input_bytes
        )
        if rubric
        else None
    )
    shadow = ShadowDecisionProvider(rubric_provider or decision)
    start = time.monotonic()
    try:
        report = review_pull_request(evidence, policy, shadow, reviewer)
        session.assert_complete()
        artifact = {
            "experiment_version": 1,
            "fingerprint": experiment_fingerprint,
            "adapter_revision": versions_fingerprint(),
            "versions": {
                key: value for key, value in adapter_versions().items() if key.endswith("_version")
            },
            "mode": "replay" if replay else "live",
            "recording_origin": session.tape.origin if session.tape else None,
            "providers": config.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "original_decisions": [
                item.model_dump(mode="json") for item in shadow.original_decisions
            ],
            "decision": (
                decision.diagnostics() if isinstance(decision, OllayaDecisionProvider) else None
            ),
            "review": reviewer.diagnostics()
            if isinstance(reviewer, OllamaReviewProvider)
            else {"responses": mock_reviewer.responses}
            if mock_reviewer
            else None,
            "request_seconds": sum(item.elapsed_seconds for item in session.exchanges),
            "run_seconds": time.monotonic() - start,
            "http_calls": len(session.exchanges),
            "schema_failures": sum(
                item.schema_failures
                for item in (decision, reviewer)
                if isinstance(item, OllayaDecisionProvider | OllamaReviewProvider)
            ),
            "transport_failures": sum(
                item.error is not None or item.status is None or not 200 <= item.status < 300
                for item in session.exchanges
            ),
            "report": report.model_dump(mode="json"),
        }
        if rubric_provider:
            artifact["rubric_request_seconds"] = sum(
                item.elapsed_seconds
                for item in session.exchanges
                if item.request.url.endswith("/v1/systemone")
            )
            artifact["rubric_schema_failures"] = decision.schema_failures
            artifact["assessment_rubric"] = rubric_provider.artifact().model_dump(mode="json")
            artifact["assessment_rubric_config"] = rubric.model_dump(mode="json")
            artifact["supplied_review"] = review_text
            artifact["rubric_prompt_version"] = rubric_versions()["prompt_version"]
            artifact["rubric_revision"] = fingerprint(rubric_versions())
        return ExperimentResult(report, artifact, session.recording() if record else None)
    finally:
        session.close()


class ExpectedConcern(Contract):
    """Reference finding anchor for automated matching and subsequent human assessment."""

    concern_id: Text
    path: Text
    line: Annotated[int, Field(gt=0)] | None = None
    description: Text


class RubricExpectations(Contract):
    """Reference rubric advice and individual criterion labels, separate from responses."""

    recommendation: Recommendation
    criteria: dict[str, Status]


class Expectations(Contract):
    """Reference routes and supported concern locations, independent of model output."""

    decision_routes: list[Literal["skip_review", "review", "needs_human_review"]] = Field(
        min_length=1
    )
    concerns: list[ExpectedConcern] = Field(default_factory=list)
    rubric: RubricExpectations | None = None


class EvaluationCase(Contract):
    """One sanitized evaluation case referencing evidence beneath the corpus directory."""

    case_id: Text
    category: Literal[
        "editorial", "correct_code", "planted_defect", "ambiguous", "incomplete", "rubric"
    ]
    evidence: Text
    review: Text | None = None
    expectations: Expectations

    @field_validator("case_id")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        """Keep case identifiers safe as output basenames.

        :param value: Corpus case identifier.
        :returns: Safe identifier for reports and recordings.
        :raises ValueError: If the identifier contains path components or unsafe characters.
        """
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
            raise ValueError("case_id must be a lowercase filename-safe identifier")
        return value


class Corpus(Contract):
    """Versioned labels with an explicit indication of whether humans reviewed them."""

    corpus_version: Literal[1] = 1
    label_status: Literal["provisional", "human_reviewed"] = "provisional"
    cases: list[EvaluationCase] = Field(min_length=1)
    assessment_rubric: Text | None = None

    @model_validator(mode="after")
    def unique_cases(self) -> "Corpus":
        """Reject duplicate identifiers before outputs can overwrite each other.

        :returns: Corpus with unique case identifiers.
        :raises ValueError: If two cases share an identifier.
        """
        identifiers = [case.case_id for case in self.cases]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("corpus case identifiers must be unique")
        if self.assessment_rubric is None and any(
            case.review is not None or case.expectations.rubric is not None for case in self.cases
        ):
            raise ValueError("review inputs and rubric labels require an assessment rubric")
        return self


def load_corpus(path: Path) -> tuple[Corpus, list[PullRequestEvidence], list[Path]]:
    """Validate all corpus evidence before running any live inference.

    :param path: JSON corpus containing labels and relative evidence paths.
    :returns: Corpus, validated evidence, and protected source paths in case order.
    :raises ValueError: If labels/evidence are invalid or a path leaves the corpus directory.
    :raises OSError: If a source cannot be read.
    """
    corpus = Corpus.model_validate_json(path.read_bytes())
    evidence = []
    sources = []
    root = path.resolve().parent
    for case in corpus.cases:
        source = (root / case.evidence).resolve()
        if not source.is_relative_to(root):
            raise ValueError("corpus evidence paths must stay beneath the corpus directory")
        item = PullRequestEvidence.model_validate_json(source.read_bytes())
        paths = {file.path for file in item.files}
        if any(concern.path not in paths for concern in case.expectations.concerns):
            raise ValueError("reference concern must point to a changed file")
        evidence.append(item)
        sources.append(source)
    return corpus, evidence, sources


def evaluate_case(case: EvaluationCase, result: ExperimentResult) -> dict[str, Any]:
    """Compare uncapped model results to reference labels using conservative anchor matching.

    :param case: Reference routes and concern anchors for this evidence.
    :param result: Experiment outputs containing original decisions and all review attempts.
    :returns: Per-case counts and identifiers for inspection, not a calibrated quality claim.
    """
    artifact = result.artifact
    originals = artifact["original_decisions"]
    recommendation = originals[-1]["recommendation"] if originals else None
    native_reviews = artifact["review"]["responses"] if artifact["review"] else []
    findings = [
        finding for response in native_reviews for finding in response["result"]["findings"]
    ]
    if not artifact["review"]:
        findings = [finding.model_dump(mode="json") for finding in result.report.findings]
    # Count repeated standard/deep findings once, before the report's export cap.
    unique = {fingerprint(finding): finding for finding in findings}.values()
    matched = set()
    unsupported = 0
    for finding in unique:
        anchors = [
            concern
            for concern in case.expectations.concerns
            if concern.path == finding["path"]
            and (concern.line is None or concern.line == finding["line"])
        ]
        if anchors:
            matched.update(concern.concern_id for concern in anchors)
        else:
            unsupported += 1
    missed = [
        concern.concern_id
        for concern in case.expectations.concerns
        if concern.concern_id not in matched
    ]
    metrics = {
        "case_id": case.case_id,
        "category": case.category,
        "original_recommendation": recommendation,
        "route_matches_reference": (
            recommendation in case.expectations.decision_routes if recommendation else None
        ),
        "unsafe_skip_recommendations": int(
            recommendation == "skip_review"
            and "skip_review" not in case.expectations.decision_routes
        ),
        "missed_planted_defects": len(missed) if case.category == "planted_defect" else 0,
        "missed_concerns": missed,
        "unsupported_findings": unsupported,
        "human_handoffs": int(result.report.outcome == "needs_human_review"),
        "schema_failures": artifact["schema_failures"],
        "transport_failures": artifact["transport_failures"],
        "request_seconds": artifact["request_seconds"],
        "outcome": result.report.outcome,
    }
    if case.expectations.rubric:
        expected = case.expectations.rubric
        assessment = artifact.get("assessment_rubric", {})
        statuses = {item["criterion_id"]: item["status"] for item in assessment.get("criteria", [])}
        mismatches = [
            identifier
            for identifier, status in expected.criteria.items()
            if statuses.get(identifier) != status
        ]
        recommendation_matches = assessment.get("recommendation") == expected.recommendation
        metrics["rubric"] = {
            "recommendation": assessment.get("recommendation"),
            "recommendation_matches_reference": recommendation_matches,
            "criterion_mismatches": mismatches,
            "blockers": assessment.get("blockers", []),
            "suggestions": assessment.get("suggestions", []),
            "criteria": statuses,
            "native_answers": {
                item["criterion_id"]: item.get("native_answer")
                for item in assessment.get("criteria", [])
                if item.get("native_answer") is not None
            },
            "question_types": {
                item["criterion_id"]: item.get("question_type", "choice")
                for item in artifact.get("assessment_rubric_config", {}).get("criteria", [])
                if item["check"] == "semantic"
            },
            "skip_recommended": assessment.get("skip_recommended", False),
            "approval_recommended": assessment.get("approval_recommended", False),
            "automation_authorized": False,
        }
        metrics["rubric_request_seconds"] = artifact.get("rubric_request_seconds", 0.0)
        metrics["rubric_schema_failures"] = artifact.get("rubric_schema_failures", 0)
        metrics["rubric_criterion_mismatches"] = len(mismatches)
        metrics["rubric_recommendation_mismatches"] = int(not recommendation_matches)
        metrics["rubric_blocks"] = int(assessment.get("recommendation") == "block")
        metrics["rubric_suggestions"] = len(assessment.get("suggestions", []))
        metrics["rubric_skip_approval_recommendations"] = int(
            assessment.get("approval_recommended", False)
        )
        metrics["unsafe_rubric_approvals"] = int(
            assessment.get("approval_recommended", False)
            and (
                expected.recommendation != "skip_and_approve"
                or any(status != "passed" for status in expected.criteria.values())
            )
        )
    return metrics


def load_corpus_rubric(
    path: Path, corpus: Corpus, *, rubric_path: Path | None = None
) -> tuple[AssessmentRubric | None, list[str], list[Path]]:
    """Load and validate rubric and supplied reviews before any inference or output.

    :param path: Corpus path defining the allowed input directory.
    :param corpus: Validated corpus with optional rubric and review paths.
    :param rubric_path: Explicit alternative rubric, keeping evidence and labels fixed.
    :returns: Rubric, review texts in case order, and additional protected input paths.
    :raises ValueError: If paths escape the corpus directory or labels differ from the rubric.
    :raises OSError: If an input cannot be read.
    """
    root = path.resolve().parent
    sources = []

    def source(relative: str) -> Path:
        """Resolve one additional input without leaving the corpus directory.

        :param relative: Relative rubric or supplied-review path.
        :returns: Protected source path beneath the corpus directory.
        :raises ValueError: If the path escapes that directory.
        """
        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("rubric and review paths must stay beneath the corpus directory")
        sources.append(resolved)
        return resolved

    if rubric_path is not None:
        if corpus.assessment_rubric is None:
            raise ValueError("an alternative rubric requires an existing rubric corpus")
        sources.append(rubric_path.resolve())
        rubric = load_assessment_rubric(rubric_path)
    else:
        rubric = (
            load_assessment_rubric(source(corpus.assessment_rubric))
            if corpus.assessment_rubric
            else None
        )
    texts = []
    for case in corpus.cases:
        texts.append(source(case.review).read_text(encoding="utf-8") if case.review else "")
        if case.expectations.rubric:
            ids = {criterion.criterion_id for criterion in rubric.criteria}
            if set(case.expectations.rubric.criteria) != ids:
                raise ValueError("reference rubric labels must cover every configured criterion")
    return rubric, texts, sources


def summarize_evaluation(corpus: Corpus, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate evaluation counts without treating replay or provisional labels as calibration.

    :param corpus: Label provenance and case definitions.
    :param results: Per-case metrics in corpus order.
    :returns: Summary including reference labels and evaluation limitations.
    """
    counts = (
        "unsafe_skip_recommendations",
        "missed_planted_defects",
        "unsupported_findings",
        "human_handoffs",
        "schema_failures",
        "transport_failures",
        "request_seconds",
    )
    if any("rubric" in result for result in results):
        counts += (
            "rubric_request_seconds",
            "rubric_schema_failures",
            "rubric_criterion_mismatches",
            "rubric_recommendation_mismatches",
            "unsafe_rubric_approvals",
            "rubric_blocks",
            "rubric_suggestions",
            "rubric_skip_approval_recommendations",
        )
    return {
        "evaluation_version": 1,
        "label_status": corpus.label_status,
        "case_count": len(results),
        "totals": {name: sum(result.get(name, 0) for result in results) for name in counts},
        "cases": results,
        "reference_labels": corpus.model_dump(mode="json"),
        "limitations": (
            "Location matching is a proxy: matched findings still require human assessment. "
            "Unmatched findings are counted as unsupported by these reference labels. "
            "Replay validates integration behavior, not current model quality."
        ),
    }
