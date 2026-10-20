"""Decider-only live execution and isolated, exact assessment replay."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import httpx2

from .assessment_engine import AssessmentReport, assess_pull_request
from .assessment_rubric import (
    COMMUNICATION_INSTRUCTIONS,
    AssessmentRubric,
    RubricDecisionProvider,
    rubric_versions,
)
from .config import Policy
from .contracts import PullRequestEvidence
from .hosted_providers import JevDecisionProvider
from .llm_decisions import OllamaDecisionProvider, llm_decision_versions
from .local_providers import OllayaDecisionProvider, SystemOneDecisionProvider, adapter_versions
from .provider_config import DecisionSettings, JevSettings, MockSettings, ReviewSettings
from .transport import Session, Tape, fingerprint

DecisionConfig = DecisionSettings | JevSettings | ReviewSettings | MockSettings


@dataclass
class AssessmentRun:
    """An authoritative assessment and separate execution diagnostics."""

    report: AssessmentReport
    artifact: dict[str, Any]
    tape: Tape | None


def run_assessment(
    evidence: PullRequestEvidence,
    policy: Policy,
    rubric: AssessmentRubric,
    settings: DecisionConfig | None = None,
    *,
    replay: Path | None = None,
    record: bool = False,
    http_transport: httpx.AsyncBaseTransport | None = None,
    hosted_transport: httpx2.BaseTransport | None = None,
) -> AssessmentRun:
    """Run only communication criteria; never construct a reviewer or reporter.

    :param evidence: Complete PR metadata and patches, without reference annotations.
    :param policy: Input budgets and deterministic gates.
    :param rubric: Explicit communication requirements.
    :param settings: Decider settings, defaulting to an offline unresolved semantic mock.
    :param replay: Exact assessment-only tape, with no live fallback.
    :param record: Explicitly capture sanitized live exchanges.
    :param http_transport: Optional synthetic local HTTP boundary for tests.
    :param hosted_transport: Optional synthetic hosted SDK boundary for tests.
    :returns: Final report, provenance, and optional tape.
    :raises ValueError: If recording consent or input configuration is invalid.
    """
    settings = settings or MockSettings()
    if replay and record:
        raise ValueError("recording and replay are mutually exclusive")
    if record and isinstance(settings, JevSettings) and not settings.allow_recording:
        raise ValueError("hosted recording requires allow_recording=true")
    if any(c.target == "review" for c in rubric.criteria):
        raise ValueError("communication rubrics cannot assess supplied reviews")
    inputs = {
        "pipeline": "pr-communication-v1",
        "decision": settings.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "rubric": rubric.model_dump(mode="json"),
        "evidence": evidence.model_dump(mode="json"),
        "instructions": COMMUNICATION_INSTRUCTIONS,
        "rubric_versions": rubric_versions(),
        "adapters": adapter_versions(),
        "schema": AssessmentReport.model_json_schema(),
    }
    if isinstance(settings, ReviewSettings):
        inputs["llm_decision_versions"] = llm_decision_versions()
    run_fingerprint = fingerprint(inputs)
    session = Session(run_fingerprint, replay=replay, http_transport=http_transport)
    provider: SystemOneDecisionProvider | None = None
    try:
        if any(c.check == "semantic" for c in rubric.criteria):
            if isinstance(settings, DecisionSettings):
                provider = OllayaDecisionProvider(settings, session)
            elif isinstance(settings, JevSettings):
                provider = JevDecisionProvider(settings, session, transport=hosted_transport)
            elif isinstance(settings, ReviewSettings):
                provider = OllamaDecisionProvider(settings, session)
        assessor = RubricDecisionProvider(
            provider, rubric, "", max_input_bytes=policy.max_input_bytes, communication=True
        )
        report = assess_pull_request(evidence, policy, rubric, assessor)
        session.assert_complete()
        artifact = {
            "pipeline": "pr-communication-v1",
            "fingerprint": run_fingerprint,
            "mode": "replay" if replay else "live" if provider else "offline",
            "decision": settings.model_dump(mode="json"),
            "rubric": rubric.model_dump(mode="json"),
            "diagnostics": provider.diagnostics() if provider else None,
            "http_calls": sum(exchange.attempts for exchange in session.exchanges),
            "recording_origin": session.tape.origin if session.tape else None,
        }
        return AssessmentRun(report, artifact, session.recording() if record else None)
    finally:
        if isinstance(provider, JevDecisionProvider):
            provider.close()
        session.close()
