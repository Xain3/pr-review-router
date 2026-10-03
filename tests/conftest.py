import json
from pathlib import Path

import pytest

from pr_review_router.contracts import PullRequestEvidence


@pytest.fixture
def evidence():
    path = Path(__file__).resolve().parents[1] / "examples" / "evidence.json"
    return PullRequestEvidence.model_validate(json.loads(path.read_text(encoding="utf-8")))
