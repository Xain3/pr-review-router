"""Opt-in hosted System One inference with private, bounded SDK exchanges."""

import json
import logging
import os
import time
from importlib.metadata import version
from typing import Any

import httpx2
from typesafe_sdk import (
    RetryPolicy,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAPITimeoutError,
    TypeSafeClient,
    TypeSafeError,
)

from .local_providers import SystemOneDecisionProvider
from .provider_config import JevSettings
from .transport import Exchange, Request, Session, TransportFailure, canonical_json


class _BoundedTransport(httpx2.BaseTransport):
    """Bound SDK response allocation and prevent redirects or ambient proxy use."""

    def __init__(self, settings: JevSettings, transport: httpx2.BaseTransport | None) -> None:
        """Wrap an explicit test transport or a proxy-independent HTTPS transport.

        :param settings: Response and timeout budgets.
        :param transport: Optional offline SDK-native transport.
        """
        self.settings = settings
        self.transport = (
            transport if transport is not None else httpx2.HTTPTransport(trust_env=False)
        )
        self.attempts = 0
        self.deadline = float("inf")

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        """Read at most the configured response budget during one SDK attempt.

        :param request: SDK-owned authenticated request.
        :returns: Bounded response consumed by the SDK.
        :raises TransportFailure: If the endpoint, encoding, or size is invalid.
        :raises httpx2.TimeoutException: If the retry deadline is exhausted.
        """
        if str(request.url) != self.settings.endpoint + "/v1/systemone" or request.method != "POST":
            raise TransportFailure("hosted SDK attempted an unexpected endpoint")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise httpx2.TimeoutException("hosted retry budget exhausted")
        request.extensions["timeout"] = dict.fromkeys(
            ("connect", "read", "write", "pool"), min(remaining, self.settings.timeout_seconds)
        )
        request.headers["Accept-Encoding"] = "identity"
        self.attempts += 1
        response = self.transport.handle_request(request)
        try:
            if response.headers.get("content-encoding", "identity") != "identity":
                raise TransportFailure("hosted response uses an unsupported encoding")
            data = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() >= self.deadline:
                    raise httpx2.TimeoutException("hosted retry budget exhausted")
                if len(data) + len(chunk) > self.settings.max_response_bytes:
                    raise TransportFailure("hosted response exceeds the configured budget")
                data.extend(chunk)
            return httpx2.Response(
                response.status_code, headers=response.headers, content=bytes(data)
            )
        finally:
            response.close()

    def close(self) -> None:
        """Release the reusable underlying transport."""
        self.transport.close()


class JevDecisionProvider(SystemOneDecisionProvider[JevSettings]):
    """Evaluate routing and semantic criteria without authorizing automated acceptance."""

    def __init__(
        self,
        settings: JevSettings,
        session: Session,
        *,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Bind hosted settings without reading credentials or making requests yet.

        :param settings: Explicit consent, model pin, and hosted budgets.
        :param session: Shared recording or offline replay session.
        :param transport: Optional SDK-native offline test transport.
        """
        super().__init__(settings, session)
        self.settings = settings
        self.transport = transport
        self.client: TypeSafeClient | None = None
        self.bounded_transport: _BoundedTransport | None = None
        self.calls: list[dict[str, Any]] = []

    @property
    def backend_name(self) -> str:
        """Return the hosted provider's stable provenance name.

        :returns: The Jev backend identifier.
        """
        return "jev"

    def validate_backend_model(self, model: str) -> None:
        """Reject a resolved model that differs from the configured pin.

        :param model: Requested or resolved model identifier.
        :raises TransportFailure: If the model differs from the configured version.
        """
        if model != self.settings.model:
            raise TransportFailure("hosted response model differs from the configured pin")

    def _client(self) -> TypeSafeClient:
        """Lazily initialize one authenticated SDK client after evidence preflight.

        :returns: Reusable synchronous SDK client.
        :raises TransportFailure: If the named environment credential is missing.
        """
        logging.getLogger("typesafe_sdk").disabled = True
        if self.client is None:
            key = os.environ.get(self.settings.api_key_env, "").strip()
            if not key:
                raise TransportFailure("hosted API credential is missing")
            self.bounded_transport = _BoundedTransport(self.settings, self.transport)
            self.client = TypeSafeClient(
                api_key=key,
                model=self.settings.model,
                base_url=self.settings.endpoint,
                timeout=self.settings.timeout_seconds,
                retry=RetryPolicy(
                    max_retries=self.settings.max_retries,
                    timeout=self.settings.retry_budget_seconds,
                    respect_retry_after=True,
                ),
                compression=False,
                http_client=httpx2.Client(
                    transport=self.bounded_transport,
                    trust_env=False,
                    follow_redirects=False,
                ),
            )
        return self.client

    def evaluate(self, payload: dict[str, Any]) -> str:
        """Evaluate and normalize a complete question batch, or replay without credentials.

        :param payload: Model, complete evidence state, and typed questions.
        :returns: Normalized JSON with all requested answers and safe call provenance.
        :raises TransportFailure: If budgets, SDK validation, or hosted inference fail.
        """
        self.validate_backend_model(payload["model"])
        if len(canonical_json(payload).encode("utf-8")) > self.settings.max_request_bytes:
            raise TransportFailure("complete hosted input exceeds the configured request budget")
        for identifier, question in payload["questions"].items():
            single = {**payload, "questions": {identifier: question}}
            if len(canonical_json(single).encode("utf-8")) > self.settings.max_question_bytes:
                raise TransportFailure("complete hosted input exceeds the per-question budget")
            if (
                question["type"] == "choice"
                and len(question["criteria"]) > self.settings.max_choice_options
            ):
                raise TransportFailure("hosted choice options exceed the configured limit")
        if self.session.tape:
            try:
                content = self.session.request(self.settings, "POST", "/v1/systemone", payload)
            finally:
                if self.session.exchanges and self.session.exchanges[-1].schema_failure:
                    self.schema_failures += 1
        else:
            content = self._evaluate_live(payload)
        data = json.loads(content)
        self.validate_backend_model(data["model"])
        self.metadata[data["model"]] = {
            "model": data["model"],
            "sdk_version": version("typesafe-sdk"),
        }
        self.calls.append(
            {
                "model": data["model"],
                "request_id": data.get("request_id"),
                "usage": data.get("usage", {}),
                "attempts": self.session.exchanges[-1].attempts,
            }
        )
        return content

    def _evaluate_live(self, payload: dict[str, Any]) -> str:
        """Record one final SDK result without headers or upstream failure text.

        :param payload: Bounded, complete request.
        :returns: Strictly normalized response JSON.
        :raises TransportFailure: On any SDK, response, or transport failure.
        """
        try:
            client = self._client()
        except (TypeSafeError, httpx2.HTTPError, ValueError):
            raise TransportFailure("hosted client configuration is invalid") from None
        start = time.monotonic()
        status = None
        content = None
        error = None
        schema_failure = False
        try:
            assert self.bounded_transport is not None
            self.bounded_transport.attempts = 0
            self.bounded_transport.deadline = start + self.settings.retry_budget_seconds
            result = client.system_one(
                state=payload["state"], questions=payload["questions"], model=payload["model"]
            )
            raw = result.raw_http_response.json()
            if not isinstance(raw, dict) or raw.get("state_truncated", False) is not False:
                raise TransportFailure("hosted server truncated or invalidated evidence")
            if set(result.answers) != set(payload["questions"]) or set(
                raw.get("answers", {})
            ) != set(result.answers):
                raise TransportFailure("hosted response does not answer every requested question")
            for identifier, question in payload["questions"].items():
                if result.answers[identifier].type != question["type"]:
                    raise TransportFailure("hosted answer type differs from the requested type")
            self.validate_backend_model(result.model)
            data = result.model_dump(mode="json")
            data["usage"] = {
                key: value for key, value in data["usage"].items() if value is not None
            }
            data["request_id"] = result.raw_http_response.headers.get("x-typesafe-request-id")
            content = canonical_json(data)
            if len(content.encode("utf-8")) > self.settings.max_response_bytes:
                content = None
                raise TransportFailure("normalized hosted response exceeds the configured budget")
            status = 200
        except TypeSafeAPIResponseValidationError:
            self.schema_failures += 1
            schema_failure = True
            error = "encoding"
        except TypeSafeAPITimeoutError:
            error = "timeout"
        except TypeSafeAPIError as failure:
            status = failure.status
        except (TypeSafeError, httpx2.HTTPError, TransportFailure, ValueError, TypeError, KeyError):
            error = "network"
        self.session.exchanges.append(
            Exchange(
                request=Request(
                    method="POST", url=self.settings.endpoint + "/v1/systemone", body=payload
                ),
                status=status,
                body=content,
                error=error,
                elapsed_seconds=time.monotonic() - start,
                attempts=max(1, self.bounded_transport.attempts) if self.bounded_transport else 1,
                schema_failure=schema_failure,
            )
        )
        if content is None:
            raise TransportFailure("hosted inference failed; no assessment is available") from None
        return content

    def diagnostics(self) -> dict[str, Any]:
        """Include request IDs, usage, and attempt counts without authentication headers.

        :returns: Normalized answer and operational provenance.
        """
        return {**super().diagnostics(), "calls": self.calls}

    def close(self) -> None:
        """Release the reused SDK client and its transport."""
        if self.client is not None:
            self.client.close()
        elif self.bounded_transport is not None:
            self.bounded_transport.close()
