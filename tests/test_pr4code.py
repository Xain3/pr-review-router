import json

import pytest

from pr_review_router.contracts import PullRequestEvidence
from pr_review_router.pr4code import PR4CodeReviewProvider
from pr_review_router.providers import ProviderSchemaError, request_review

pytestmark = pytest.mark.unit

EVIDENCE = PullRequestEvidence.model_validate(
    json.load(open("examples/diff/evidence.json", encoding="utf-8"))
)


def test_valid_response_is_validated_and_request_is_authenticated():
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=json.loads(body))
        return json.dumps(
            {
                "outcome": "no_concerns",
                "confidence": {"value": 0.8, "source": "self_reported"},
                "summary": "ok",
                "provider": "pr4code",
            }
        ).encode()

    provider = PR4CodeReviewProvider("https://x.test/review", "k", transport=transport)
    result = request_review(provider, EVIDENCE, depth="deep")
    assert result.outcome == "no_concerns"
    assert seen["body"]["depth"] == "deep"
    assert seen["headers"]["Authorization"] == "Bearer k"


def test_invalid_response_fails_schema():
    provider = PR4CodeReviewProvider("https://x.test", "k", transport=lambda *a: b'{"x": 1}')
    with pytest.raises(ProviderSchemaError):
        request_review(provider, EVIDENCE, depth="standard")


def test_transport_failure_hides_cause():
    def boom(*args):
        raise OSError("secret-token-leak")

    provider = PR4CodeReviewProvider("https://x.test", "k", transport=boom)
    with pytest.raises(RuntimeError) as info:
        provider.review(EVIDENCE, depth="standard")
    assert "secret" not in str(info.value)
