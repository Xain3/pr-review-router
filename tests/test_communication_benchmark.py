"""Keep communication benchmark inputs independent from preserved legacy reviews."""

import json
import shutil

import pytest
from test_cli import ROOT

from pr_review_router.assessment_rubric import load_assessment_rubric
from pr_review_router.communication_benchmark import run_benchmark
from pr_review_router.provider_config import MockSettings

pytestmark = pytest.mark.unit


def test_communication_benchmark_uses_frozen_partitions_and_no_review_labels(tmp_path):
    root = ROOT / "examples/synthetic-prs"
    config = load_assessment_rubric(root / "communication-rubric.toml")
    summary = run_benchmark(root, config, MockSettings(), tmp_path / "benchmark")
    assert summary["support"] == 24
    assert summary["reference_version"] == "author-communication-v1"
    assert "review-coverage" not in summary["criterion_confusion"]
    assert summary["false_acceptance"] == 0
    assert summary["unresolved"] > 0
    assert all(
        "review" not in json.loads(p.read_text())["rubric"]["criteria"][0]["target"]
        for p in (tmp_path / "benchmark").glob("*.provenance.json")
    )
    with pytest.raises(ValueError, match="outside"):
        run_benchmark(root, config, MockSettings(), root / "outputs")


def test_legacy_review_changes_cannot_change_communication_results(tmp_path):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT / "examples/synthetic-prs", root)
    config = load_assessment_rubric(root / "communication-rubric.toml")
    before = run_benchmark(root, config, MockSettings(), tmp_path / "before")
    for path in root.glob("prs/*/review.md"):
        path.write_text("A completely different legacy review.")
    for path in root.glob("prs/*/annotations.json"):
        raw = json.loads(path.read_text())
        raw["criteria"]["review-coverage"]["status"] = "failed"
        path.write_text(json.dumps(raw))
    after = run_benchmark(root, config, MockSettings(), tmp_path / "after")
    assert before["cases"] == after["cases"]
    assert before["reference_hash"] != after["reference_hash"]
    for path in (tmp_path / "before").glob("*.provenance.json"):
        assert (
            json.loads(path.read_text())["fingerprint"]
            == json.loads((tmp_path / "after" / path.name).read_text())["fingerprint"]
        )
