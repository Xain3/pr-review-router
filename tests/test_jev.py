"""Offline hosted-provider configuration and SDK boundary checks."""

import json
import logging
from pathlib import Path

import httpx2
import pytest
from local_model_helpers import fixture_evidence
from pydantic import ValidationError

from pr_review_router.assessment_rubric import AssessmentRubric
from pr_review_router.config import Policy
from pr_review_router.experiments import run_experiment
from pr_review_router.provider_config import DecisionSettings, JevSettings, ProvidersConfig
from pr_review_router.transport import ReplayMismatch

pytestmark = pytest.mark.unit


def hosted_response(request):
    """Construct invented typed answers from the actual SDK request.

    :param request: SDK-native outgoing request.
    :returns: Synthetic response; no model inference occurs.
    """
    payload = json.loads(request.content)
    answers = {}
    for identifier, question in payload["questions"].items():
        kind = question["type"]
        if kind == "choice":
            labels = list(question["criteria"])
            choice = "skip_review" if "skip_review" in labels else "passed"
            answers[identifier] = {
                "type": kind,
                "choice": choice,
                "confidence": 0.99,
                "probabilities": {label: float(label == choice) for label in labels},
            }
        elif kind == "noul":
            answers[identifier] = {"type": kind, "noul": 0.99}
        else:
            levels = question["criteria"]
            answers[identifier] = {
                "type": kind,
                "score": float(len(levels) - 1),
                "confidence": 0.99,
                "legend": dict(enumerate(levels)),
                "probabilities": {
                    str(index): float(index == len(levels) - 1) for index in range(len(levels))
                },
            }
    return httpx2.Response(
        200,
        json={
            "model": payload["model"],
            "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 0},
        },
        headers={"x-typesafe-request-id": "synthetic-request"},
    )


@pytest.fixture
def hosted_config(monkeypatch):
    """Enable a synthetic credential and disable retries except in retry-specific tests.

    :param monkeypatch: Isolated test environment.
    :returns: Explicitly opted-in test configuration.
    """
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-private-key")
    return ProvidersConfig(
        decision=JevSettings(allow_network=True, allow_recording=True, max_retries=0)
    )


def run_hosted(config, handler=hosted_response, **kwargs):
    """Run actual hosted adapter and SDK code with no inference network access.

    :param config: Explicit provider configuration.
    :param handler: Synthetic HTTP responder.
    :param kwargs: Additional experiment options.
    :returns: Complete experiment result.
    """
    evidence = kwargs.pop("evidence", fixture_evidence())
    return run_experiment(
        evidence, Policy(), config, hosted_transport=httpx2.MockTransport(handler), **kwargs
    )


@pytest.mark.parametrize("kind", ["choice", "noul", "predicate", "score"])
def test_hosted_rubric_batches_typed_questions(hosted_config, kind):
    """Use Jev for semantic criteria without changing reviewer responsibilities."""
    criterion = {
        "criterion_id": "meaning",
        "description": "Description explains the change.",
        "check": "semantic",
        "action": "suggest",
        "suggestion": "Explain the change.",
        "instructions": "Assess description meaning.",
        "question_type": kind,
    }
    if kind != "choice":
        criterion.update(fail_threshold=0.2, pass_threshold=0.8)
    if kind == "score":
        criterion.update(levels=["absent", "partial", "complete"], pass_threshold=1.5)
    rubric = AssessmentRubric.model_validate({"criteria": [criterion]})
    result = run_hosted(hosted_config, rubric=rubric)
    assessment = result.artifact["assessment_rubric"]
    assert assessment["criteria"][0]["status"] == "passed"
    assert not assessment["automation_authorized"]
    assert result.artifact["original_decisions"][0]["provider"] == "rubric:jev:jev-1.14.0"
    answer = assessment["criteria"][0]["native_answer"]
    if kind == "score":
        assert answer["legend"] == {"0": "absent", "1": "partial", "2": "complete"}
        assert set(answer["probabilities"]) == {"0", "1", "2"}
    if kind in {"noul", "predicate"}:
        assert "confidence" not in answer


def test_hosted_score_confidence_abstention(hosted_config):
    """Map low native Score confidence to uncertainty without inventing task confidence."""
    rubric = AssessmentRubric.model_validate(
        {
            "criteria": [
                {
                    "criterion_id": "meaning",
                    "description": "Description explains the change.",
                    "check": "semantic",
                    "action": "suggest",
                    "suggestion": "Explain the change.",
                    "instructions": "Assess meaning.",
                    "question_type": "score",
                    "levels": ["absent", "partial", "complete"],
                    "fail_threshold": 0.2,
                    "pass_threshold": 1.5,
                    "uncertain_below_confidence": 0.8,
                }
            ]
        }
    )

    def handle(request):
        body = hosted_response(request).json()
        body["answers"]["meaning"]["confidence"] = 0.5
        return httpx2.Response(200, json=body)

    result = run_hosted(hosted_config, handle, rubric=rubric)
    assert result.artifact["assessment_rubric"]["criteria"][0]["status"] == "uncertain"


@pytest.mark.parametrize("status", [401, 403, 413, 429, 503])
def test_hosted_http_failures_are_private_and_fail_closed(hosted_config, status):
    """Never expose upstream error bodies or accept a failed assessment."""
    result = run_hosted(
        hosted_config,
        lambda request: httpx2.Response(status, json={"error": "private upstream body"}),
        record=True,
    )
    assert result.report.outcome == "needs_human_review"
    assert not result.artifact["original_decisions"]
    assert "private upstream body" not in result.report.model_dump_json()
    assert "private upstream body" not in result.tape.model_dump_json()


@pytest.mark.parametrize("status", [429, 503])
def test_hosted_sdk_honors_retry_after_and_records_attempts(hosted_config, status):
    """Retry transient statuses through the real SDK with zero-delay Retry-After."""
    hosted_config.decision.max_retries = 1
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(status, json={"error": "busy"}, headers={"Retry-After": "0"})
        return hosted_response(request)

    result = run_hosted(hosted_config, handle, record=True)
    assert len(requests) == 2
    assert result.artifact["http_calls"] == 2
    assert result.tape.exchanges[0].attempts == 2
    assert result.artifact["original_decisions"]


def test_hosted_retry_budget_rejects_long_retry_after(hosted_config):
    """Do not sleep beyond the configured retry budget."""
    hosted_config.decision.max_retries = 2
    result = run_hosted(
        hosted_config,
        lambda request: httpx2.Response(
            429, json={"error": "busy"}, headers={"Retry-After": "3600"}
        ),
    )
    assert result.artifact["http_calls"] == 1
    assert result.report.outcome == "needs_human_review"


def test_hosted_retry_exhaustion(hosted_config):
    """Exhaust the configured retry count without accepting an incomplete result."""
    hosted_config.decision.max_retries = 2
    result = run_hosted(
        hosted_config,
        lambda request: httpx2.Response(503, json={"error": "busy"}, headers={"Retry-After": "0"}),
    )
    assert result.artifact["http_calls"] == 3
    assert result.report.outcome == "needs_human_review"


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "unknown",
        "wrong_type",
        "wrong_labels",
        "model",
        "truncated",
        "malformed",
        "oversize",
    ],
)
def test_hosted_invalid_responses_fail_closed(hosted_config, fault):
    """Require every requested answer, exact model identity, and complete bounded evidence."""

    def handle(request):
        body = hosted_response(request).json()
        if fault == "missing":
            body["answers"] = {}
        elif fault == "unknown":
            body["answers"]["route"]["type"] = "future_kind"
        elif fault == "wrong_type":
            body["answers"]["route"] = {"type": "noul", "noul": 0.99}
        elif fault == "wrong_labels":
            body["answers"]["route"]["probabilities"] = {"other": 1.0}
        elif fault == "model":
            body["model"] = "jev-1.13.0"
        elif fault == "truncated":
            body["state_truncated"] = True
        elif fault == "malformed":
            return httpx2.Response(200, content=b"not-json")
        else:
            return httpx2.Response(
                200, content=b" " * (hosted_config.decision.max_response_bytes + 1)
            )
        return httpx2.Response(200, json=body)

    result = run_hosted(hosted_config, handle)
    assert result.report.outcome == "needs_human_review"
    assert not result.artifact["original_decisions"]


@pytest.mark.parametrize("gate", ["missing_key", "preflight", "request_budget", "question_budget"])
def test_hosted_preflight_and_budgets_never_send_evidence(hosted_config, monkeypatch, gate):
    """Reject missing credentials and incomplete or oversized evidence before sending."""
    evidence = fixture_evidence()
    if gate == "missing_key":
        monkeypatch.delenv("TYPESAFE_API_KEY")
    elif gate == "preflight":
        evidence.files[0].patch = None
    elif gate == "request_budget":
        hosted_config.decision.max_request_bytes = 1
    else:
        hosted_config.decision.max_question_bytes = 1

    def forbidden(request):
        pytest.fail("preflight must prevent HTTP requests")

    result = run_hosted(hosted_config, forbidden, evidence=evidence)
    assert result.report.outcome == "needs_human_review"
    assert result.artifact["http_calls"] == 0


def test_hosted_recording_needs_separate_consent(hosted_config):
    """Do not persist private evidence without explicit recording permission."""
    hosted_config.decision.allow_recording = False
    with pytest.raises(ValueError, match="allow_recording"):
        run_hosted(hosted_config, record=True)


def test_hosted_replay_needs_no_credentials_or_client(hosted_config, monkeypatch, tmp_path):
    """Keep hosted tapes credential-free and replay without SDK clients or network fallback."""
    original = run_hosted(hosted_config, record=True)
    text = original.tape.model_dump_json()
    assert "synthetic-private-key" not in text
    assert "authorization" not in text.lower()
    tape = tmp_path / "hosted.tape.json"
    tape.write_text(text)
    monkeypatch.delenv("TYPESAFE_API_KEY")

    def forbidden(*args, **kwargs):
        pytest.fail("replay must not create an HTTP or SDK client")

    monkeypatch.setattr("pr_review_router.hosted_providers.TypeSafeClient", forbidden)
    replay = run_hosted(hosted_config, replay=tape)
    assert replay.report == original.report
    assert replay.artifact["decision"] == original.artifact["decision"]
    hosted_config.decision.model = "jev-1.13.0"
    with pytest.raises(ReplayMismatch):
        run_hosted(hosted_config, replay=tape)


def test_sdk_debug_logging_cannot_expose_evidence(hosted_config, caplog):
    """Suppress SDK wire-body logs even when debug logging was enabled externally."""
    evidence = fixture_evidence()
    evidence.body = "private-pr-content-sentinel"
    logger = logging.getLogger("typesafe_sdk")
    logger.disabled = False
    with caplog.at_level(logging.DEBUG):
        result = run_hosted(hosted_config, evidence=evidence)
    assert result.artifact["original_decisions"]
    assert evidence.body not in caplog.text
    assert "synthetic-private-key" not in caplog.text


@pytest.mark.parametrize("failure", [httpx2.ConnectError, httpx2.ReadTimeout])
def test_hosted_connection_failures_are_bounded_and_private(hosted_config, failure):
    """Map SDK connection exceptions without exposing their messages."""

    def handle(request):
        raise failure("private connection detail", request=request)

    result = run_hosted(hosted_config, handle, record=True)
    assert result.report.outcome == "needs_human_review"
    assert result.tape.exchanges[0].attempts == 1
    assert "private connection detail" not in result.tape.model_dump_json()
    assert "private connection detail" not in result.report.model_dump_json()


def test_hosted_sdk_client_is_reused_and_closed(hosted_config, monkeypatch):
    """Reuse one SDK client across schema retry and release it after the experiment."""
    from pr_review_router.hosted_providers import TypeSafeClient

    clients = []
    requests = []

    class TrackingClient(TypeSafeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.closed = False
            clients.append(self)

        def close(self):
            self.closed = True
            super().close()

    def handle(request):
        requests.append(request)
        body = hosted_response(request).json()
        if len(requests) == 1:
            body["answers"]["route"]["confidence"] = 2.0
        return httpx2.Response(200, json=body)

    monkeypatch.setattr("pr_review_router.hosted_providers.TypeSafeClient", TrackingClient)
    result = run_hosted(hosted_config, handle)
    assert len(clients) == 1
    assert len(requests) == 2
    assert clients[0].closed
    assert result.artifact["original_decisions"]


@pytest.mark.parametrize("malformed", [False, True])
def test_hosted_failure_replay_preserves_metrics(hosted_config, monkeypatch, tmp_path, malformed):
    """Replay failed calls without credentials and retain schema and transport counters."""

    def handle(request):
        return (
            httpx2.Response(200, content=b"not-json")
            if malformed
            else httpx2.Response(401, json={"error": "denied"})
        )

    original = run_hosted(hosted_config, handle, record=True)
    tape = tmp_path / "failed.tape.json"
    tape.write_text(original.tape.model_dump_json())
    monkeypatch.delenv("TYPESAFE_API_KEY")
    replay = run_hosted(hosted_config, replay=tape)
    assert replay.report == original.report
    for key in ("http_calls", "schema_failures", "transport_failures", "request_seconds"):
        assert replay.artifact[key] == original.artifact[key]


def test_installed_cli_replays_hosted_semantic_rubric(hosted_config, monkeypatch, tmp_path):
    """Accept the hosted rubric backend in the CLI and replay an entire corpus offline."""
    from test_cli import cli

    rubric = AssessmentRubric.model_validate(
        {
            "criteria": [
                {
                    "criterion_id": "meaning",
                    "description": "Description explains the change.",
                    "check": "semantic",
                    "action": "suggest",
                    "suggestion": "Explain the change.",
                    "instructions": "Assess meaning.",
                }
            ]
        }
    )
    result = run_hosted(hosted_config, rubric=rubric, record=True)
    (tmp_path / "evidence.json").write_text(fixture_evidence().model_dump_json())
    (tmp_path / "rubric.json").write_text(rubric.model_dump_json())
    (tmp_path / "case.tape.json").write_text(result.tape.model_dump_json())
    (tmp_path / "providers.toml").write_text(
        '[decision]\nbackend="jev"\nallow_network=true\nallow_recording=true\nmax_retries=0\n'
    )
    (tmp_path / "corpus.json").write_text(
        json.dumps(
            {
                "corpus_version": 1,
                "assessment_rubric": "rubric.json",
                "cases": [
                    {
                        "case_id": "case",
                        "category": "rubric",
                        "evidence": "evidence.json",
                        "expectations": {
                            "decision_routes": ["skip_review"],
                            "concerns": [],
                            "rubric": {
                                "recommendation": "skip_and_approve",
                                "criteria": {"meaning": "passed"},
                            },
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.delenv("TYPESAFE_API_KEY")
    completed = cli(
        "evaluate",
        "--corpus",
        tmp_path / "corpus.json",
        "--providers-config",
        tmp_path / "providers.toml",
        "--replay-dir",
        tmp_path,
        "--output-dir",
        tmp_path / "outputs",
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["case_count"] == 1
    artifact = json.loads((tmp_path / "outputs/case.experiment.json").read_text())
    assert artifact["mode"] == "replay"
    assert artifact["assessment_rubric"]["criteria"][0]["status"] == "passed"


def test_documented_hosted_config_loads_offline():
    """Keep the checked-in hosted configuration valid without credentials."""
    from pr_review_router.provider_config import load_providers_config

    path = Path(__file__).resolve().parents[1] / "examples/jev/providers.toml"
    config = load_providers_config(path)
    assert isinstance(config.decision, JevSettings)
    assert not config.decision.allow_recording


def test_hosted_decision_uses_real_sdk_and_unavailable_confidence(monkeypatch) -> None:
    """Exercise SDK authentication, model identity, and native answer normalization offline."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-key")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "usage": {"input_tokens": 100, "output_tokens": 0},
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": "skip_review",
                        "confidence": 0.99,
                        "probabilities": {
                            "skip_review": 0.99,
                            "review": 0.005,
                            "needs_human_review": 0.005,
                        },
                    }
                },
            },
            headers={"x-typesafe-request-id": "offline-request"},
        )

    result = run_experiment(
        fixture_evidence(),
        Policy(),
        ProvidersConfig(decision=JevSettings(allow_network=True)),
        hosted_transport=httpx2.MockTransport(handle),
    )
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer offline-test-key"
    assert result.artifact["original_decisions"][0]["provider"] == "jev:jev-1.14.0"
    assert result.artifact["original_decisions"][0]["confidence"]["source"] == "unavailable"
    assert result.artifact["decision"]["calls"][0]["request_id"] == "offline-request"
    assert result.report.decision.recommendation == "review"


def test_hosted_configuration_requires_consent_and_version() -> None:
    """Require explicit consent and reject moving model aliases."""
    with pytest.raises(ValidationError):
        ProvidersConfig.model_validate({"decision": {"backend": "jev"}})
    with pytest.raises(ValidationError):
        JevSettings(allow_network=True, model="jev-latest")
    settings = JevSettings(allow_network=True)
    assert settings.max_choice_options == 255
    assert "api_key" not in settings.model_dump()


@pytest.mark.parametrize(
    "endpoint",
    ["http://api.typesafe.ai", "https://secret@api.typesafe.ai", "https://api.typesafe.ai/v1"],
)
def test_hosted_endpoint_validation(endpoint: str) -> None:
    """Reject insecure origins, embedded credentials, and path suffixes."""
    with pytest.raises(ValidationError):
        JevSettings(allow_network=True, endpoint=endpoint)


def test_local_providers_remain_local_only() -> None:
    """Do not relax Ollaya origin restrictions when adding hosted settings."""
    with pytest.raises(ValidationError):
        DecisionSettings(endpoint="https://api.typesafe.ai", model="jev-1.14.0")
