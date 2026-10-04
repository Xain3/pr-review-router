"""Synthetic semantic assessments for offline rubric integration tests."""

import json
from pathlib import Path

import httpx
from local_model_helpers import SyntheticServer

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/rubric-review"


class RubricServer(SyntheticServer):
    """Serve invented criterion answers without pretending to assess substantive meaning."""

    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        """Select explicit native statuses for question responses.

        :param statuses: Scripted overrides; all other criterion answers are passed.
        """
        super().__init__()
        self.statuses = statuses or {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Intercept the actual batch of semantic questions at the HTTP boundary.

        :param request: Request issued by the rubric decision provider.
        :returns: Synthetic criterion answers or existing local metadata/review fixtures.
        """
        if request.url.path != "/v1/systemone":
            return super().handle(request)
        self.requests.append(request)
        self.decision_calls += 1
        payload = json.loads(request.content)
        answers = {}
        for identifier in payload["questions"]:
            choice = self.statuses.get(identifier, "passed")
            probabilities = dict.fromkeys(["passed", "failed", "uncertain"], 0.005)
            probabilities[choice] = 0.99
            answers[identifier] = {
                "type": "choice",
                "choice": choice,
                "confidence": 0.985,
                "probabilities": probabilities,
            }
        return httpx.Response(
            200,
            json={
                "model": "fixture-decision:latest",
                "answers": answers,
                "usage": {"input_tokens": 1400, "output_tokens": 0},
            },
        )
