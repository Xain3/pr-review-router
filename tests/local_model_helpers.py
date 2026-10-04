"""Synthetic HTTP responses for adapter tests; these never perform model inference."""

import json
from pathlib import Path
from typing import Any

import httpx

from pr_review_router.contracts import PullRequestEvidence
from pr_review_router.provider_config import ProvidersConfig, load_providers_config

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "examples/local-models"


def fixture_config() -> ProvidersConfig:
    """Load the explicit synthetic model identities used in checked-in tapes.

    :returns: Independent local adapter configuration for deterministic tests.
    """
    return load_providers_config(LOCAL / "replay.toml")


def fixture_evidence(name: str = "editorial") -> PullRequestEvidence:
    """Load one sanitized evaluation case.

    :param name: Evidence basename beneath the example corpus.
    :returns: Validated PR evidence.
    """
    return PullRequestEvidence.model_validate_json((LOCAL / f"{name}.json").read_bytes())


def review_content(
    outcome: str = "no_concerns",
    findings: list[dict[str, Any]] | None = None,
    confidence: float = 0.96,
) -> dict[str, Any]:
    """Construct synthetic model-authored JSON without forged provenance.

    :param outcome: Review outcome to return.
    :param findings: Synthetic concern locations.
    :param confidence: Synthetic self-reported confidence.
    :returns: Model content accepted by the local review-output schema.
    """
    return {
        "outcome": outcome,
        "confidence": confidence,
        "summary": "Synthetic wire response for integration testing; no live review occurred.",
        "findings": findings or [],
    }


def completion(content: dict[str, Any] | str) -> dict[str, Any]:
    """Wrap synthetic content in a chat-completion envelope.

    :param content: Model content as a value or invalid JSON text for retry tests.
    :returns: OpenAI-compatible wire response.
    """
    return {
        "model": "fixture-review:latest",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": (json.dumps(content) if isinstance(content, dict) else content),
                },
            }
        ],
        "usage": {"prompt_tokens": 800, "completion_tokens": 100, "total_tokens": 900},
    }


class SyntheticServer:
    """Serve local metadata and scripted wire responses through httpx.MockTransport."""

    def __init__(
        self,
        *,
        route: str = "skip_review",
        reviews: list[dict[str, Any] | str] | None = None,
        context: int | None = 32768,
    ) -> None:
        """Configure synthetic route and review response sequence.

        :param route: Native routing label to return.
        :param reviews: Review content sequence; the final response repeats if exhausted.
        :param context: Explicit synthetic server num_ctx, or absent for failure tests.
        """
        self.route = route
        self.reviews = reviews or [review_content()]
        self.context = context
        self.requests: list[httpx.Request] = []
        self.review_calls = 0
        self.decision_calls = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Return deterministic local wire responses and record every request.

        :param request: Actual adapter HTTP request intercepted by MockTransport.
        :returns: Synthetic metadata or model response.
        :raises AssertionError: If an adapter sends an unexpected request.
        """
        self.requests.append(request)
        path = request.url.path
        decision = request.url.port == 11435
        name = "fixture-decision:latest" if decision else "fixture-review:latest"
        if path == "/api/version":
            response = {"version": "synthetic-server-v1"}
        elif path == "/api/tags":
            response = {"models": [{"name": name, "digest": "synthetic-digest-" + name}]}
        elif path == "/api/show":
            response = {
                "parameters": f"num_ctx {self.context}" if self.context else "",
                "template": "{{ .System }} {{ .Prompt }}",
                "model_info": {"fixture.context_length": 32768},
            }
        elif path == "/v1/systemone":
            self.decision_calls += 1
            probabilities = {"skip_review": 0.005, "review": 0.005, "needs_human_review": 0.005}
            probabilities[self.route] = 0.99
            response = {
                "model": name,
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": self.route,
                        "confidence": 0.985,
                        "probabilities": probabilities,
                    }
                },
                "usage": {"input_tokens": 300, "output_tokens": 0},
            }
        elif path == "/v1/chat/completions":
            response = completion(self.reviews[min(self.review_calls, len(self.reviews) - 1)])
            self.review_calls += 1
        else:
            raise AssertionError(f"Unexpected fixture endpoint: {path}")
        return httpx.Response(200, json=response)
