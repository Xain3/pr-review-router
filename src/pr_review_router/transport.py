"""Bounded HTTP exchanges with explicit recording and network-free replay."""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field

from .contracts import Contract, Text
from .provider_config import LocalSettings


def canonical_json(value: Any) -> str:
    """Serialize request and fingerprint values deterministically.

    :param value: JSON-compatible value to serialize.
    :returns: Compact JSON with stable key ordering.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def fingerprint(value: Any) -> str:
    """Hash versioned experiment inputs without storing them in the tape header.

    :param value: JSON-compatible experiment inputs.
    :returns: SHA-256 of the canonical JSON representation.
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class TransportFailure(RuntimeError):
    """A transport failed without exposing upstream exception or response text."""


class ReplayMismatch(ValueError):
    """A replay is stale, incomplete, or does not match the current request."""


class Request(Contract):
    """Exact, credential-free HTTP request stored in an exchange."""

    method: Literal["GET", "POST"]
    url: Text
    body: dict[str, Any] | None


class Exchange(Contract):
    """One bounded HTTP response or transport failure, including retry calls."""

    request: Request
    status: int | None = None
    body: str | None = None
    error: Literal["timeout", "network", "response_limit", "encoding"] | None = None
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Tape(Contract):
    """Versioned recording matched to provider, policy, evidence, prompts, and schemas."""

    format_version: Literal[1] = 1
    origin: Literal["live", "synthetic"]
    fingerprint: Text
    exchanges: list[Exchange]


class Session:
    """Share recording/replay state across independently configured providers."""

    def __init__(
        self,
        experiment_fingerprint: str,
        *,
        replay: Path | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Open either a local HTTP client or a strict replay with no client.

        :param experiment_fingerprint: Hash of all behavior-affecting experiment inputs.
        :param replay: Optional tape to replay without network fallback.
        :param http_transport: Injectable HTTP transport for deterministic adapter tests.
        :raises ReplayMismatch: If the tape header does not match the experiment.
        """
        self.fingerprint = experiment_fingerprint
        self.exchanges: list[Exchange] = []
        self.cursor = 0
        self.mismatch = False
        self.tape = Tape.model_validate_json(replay.read_bytes()) if replay else None
        if self.tape and self.tape.fingerprint != experiment_fingerprint:
            raise ReplayMismatch("replay fingerprint does not match the experiment")
        self.http_transport = http_transport

    def close(self) -> None:
        """Finish a session whose per-request clients have already been closed."""

    def assert_complete(self) -> None:
        """Reject unmatched requests or unused exchanges even after engine escalation.

        :raises ReplayMismatch: If replay diverged from the recorded call sequence.
        """
        if self.tape and (self.mismatch or self.cursor != len(self.tape.exchanges)):
            raise ReplayMismatch("replay request sequence does not match the recording")

    def recording(self) -> Tape:
        """Build a live tape for an explicitly requested refresh.

        :returns: Tape containing the exact exchanges collected during this run.
        :raises ValueError: If this session was replaying an existing tape.
        """
        if self.tape:
            raise ValueError("replay cannot refresh recordings")
        return Tape(origin="live", fingerprint=self.fingerprint, exchanges=self.exchanges)

    def request(
        self,
        settings: LocalSettings,
        method: Literal["GET", "POST"],
        path: str,
        body: dict[str, Any] | None = None,
    ) -> str:
        """Exchange bounded JSON text or replay an exactly matching request.

        :param settings: Local origin, I/O timeout, and response-size budget.
        :param method: HTTP method to use.
        :param path: Adapter-owned absolute API path.
        :param body: JSON request payload, if any.
        :returns: Successful HTTP response body as UTF-8 text.
        :raises TransportFailure: On network, timeout, size, encoding, or HTTP failure.
        :raises ReplayMismatch: If no matching exchange exists in replay mode.
        """
        request = Request(method=method, url=settings.endpoint + path, body=body)
        if self.tape:
            if self.cursor >= len(self.tape.exchanges) or canonical_json(
                self.tape.exchanges[self.cursor].request.model_dump(mode="json")
            ) != canonical_json(request.model_dump(mode="json")):
                self.mismatch = True
                raise ReplayMismatch("replay request does not match the recording")
            exchange = self.tape.exchanges[self.cursor]
            self.cursor += 1
        else:
            exchange = asyncio.run(self._live(settings, request))
        self.exchanges.append(exchange)
        if exchange.error:
            raise TransportFailure(f"local transport failure: {exchange.error}")
        if exchange.status is None or not 200 <= exchange.status < 300:
            raise TransportFailure("local server returned an unsuccessful HTTP status")
        if (
            exchange.body is None
            or len(exchange.body.encode("utf-8")) > settings.max_response_bytes
        ):
            raise TransportFailure("local response exceeds the configured budget")
        return exchange.body

    async def _live(self, settings: LocalSettings, request: Request) -> Exchange:
        """Collect one HTTP exchange without retrying or exposing raw exceptions.

        :param settings: Timeout and response-size limits.
        :param request: Exact HTTP request to send.
        :returns: Recorded response or a bounded failure code.
        """
        start = time.monotonic()
        status = None
        body = None
        error = None
        try:
            # The outer deadline covers DNS/connect/write/headers/body together, including
            # a server that keeps sending small chunks below the per-read I/O timeout.
            async with asyncio.timeout(settings.timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self.http_transport, trust_env=False, follow_redirects=False
                ) as client:
                    async with client.stream(
                        request.method,
                        request.url,
                        content=(
                            canonical_json(request.body).encode("utf-8") if request.body else None
                        ),
                        headers={"Content-Type": "application/json", "Accept-Encoding": "identity"},
                        timeout=settings.timeout_seconds,
                    ) as response:
                        status = response.status_code
                        data = bytearray()
                        if response.headers.get("content-encoding", "identity") != "identity":
                            error = "encoding"
                        if error is None:
                            async for chunk in response.aiter_bytes():
                                if len(data) + len(chunk) > settings.max_response_bytes:
                                    error = "response_limit"
                                    break
                                data.extend(chunk)
                        if error is None:
                            body = data.decode("utf-8")
        except (httpx.TimeoutException, TimeoutError):
            error = "timeout"
            body = None
        except httpx.HTTPError:
            error = "network"
            body = None
        except UnicodeError:
            error = "encoding"
        return Exchange(
            request=request,
            status=status,
            body=body,
            error=error,
            elapsed_seconds=time.monotonic() - start,
        )
