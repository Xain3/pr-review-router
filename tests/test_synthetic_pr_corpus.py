"""Keep benchmark fixtures compatible with real router evidence and patch contracts."""

import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_synthetic_pr_corpus_integrity():
    root = Path(__file__).resolve().parents[1] / "examples" / "synthetic-prs"
    namespace = runpy.run_path(str(root / "validate.py"))
    summary = namespace["validate"](root)
    assert summary["case_count"] == 100
    assert summary["commit_count"] == 170
    assert summary["quality_counts"] == {"good": 10, "needs_revision": 90}
