"""PR4Code review-provider connector (opt-in; never part of the default offline path).

The wire format below is an assumed JSON contract for evaluation: the request
carries pull request evidence and the requested depth; the response is a
``ReviewResult``-shaped object. Anything else fails schema validation at the
provider boundary and is handled conservatively by the engine.
"""

import json
import urllib.request
from collections.abc import Callable
from typing import Any

from .contracts import PullRequestEvidence
from .providers import Depth

Transport = Callable[[str, dict[str, str], bytes, float], bytes]


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    """POST ``body`` to an HTTPS endpoint using the standard library.

    :param url: Endpoint URL; must use ``https``.
    :param headers: HTTP request headers.
    :param body: Request body bytes.
    :param timeout: Socket timeout in seconds.
    :returns: Raw response body.
    :raises ValueError: If the URL is not HTTPS.
    """
    if not url.lower().startswith("https://"):
        raise ValueError("PR4Code endpoint must use https")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


class PR4CodeReviewProvider:
    """Review provider that delegates assessment to a PR4Code HTTP endpoint."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        transport: Transport | None = None,
        timeout: float = 60.0,
    ) -> None:
        """Configure the connector.

        :param endpoint: PR4Code review endpoint URL.
        :param api_key: API key sent as a bearer token; never included in errors or reports.
        :param transport: Optional HTTP callable, injected by offline tests.
        :param timeout: Request timeout in seconds.
        """
        self._endpoint = endpoint
        self._api_key = api_key
        self._transport = transport or _urllib_transport
        self._timeout = timeout

    def review(self, evidence: PullRequestEvidence, *, depth: Depth) -> Any:
        """Request a review from PR4Code.

        :param evidence: Validated pull request metadata and file patches.
        :param depth: Review depth requested by the routing engine.
        :returns: Parsed JSON response, validated later at the provider boundary.
        :raises RuntimeError: If the transport fails; the cause is not echoed.
        """
        body = json.dumps({"depth": depth, "evidence": evidence.model_dump(mode="json")})
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        try:
            raw = self._transport(self._endpoint, headers, body.encode(), self._timeout)
            return json.loads(raw)
        except Exception:
            raise RuntimeError("PR4Code request failed") from None
