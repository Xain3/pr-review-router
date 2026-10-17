"""Preserve fresh blind review and conservative adjudication of the original corpus."""

import json
import runpy
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1] / "examples" / "synthetic-prs"


def test_fresh_legacy_review_covers_every_original_case_and_criterion():
    archive = json.loads((ROOT / "independent-review/legacy-reviews.json").read_text())
    historical = json.loads((ROOT / "independent-review/reviews.json").read_text())
    index = json.loads((ROOT / "index.json").read_text())
    validation = runpy.run_path(str(ROOT / "validate.py"))
    selected = {
        **index,
        "cases": [case for case in index["cases"] if case["split"] == "development"],
    }
    counts = validation["validate_independent_review"](
        ROOT, selected, validation["ACTIVE_REVIEW_ARCHIVES"]["development"]
    )
    assert counts["labels"] == 125 * 7
    assert set(archive["cases"]) == set(historical["cases"])
    assert archive["label_status"] == "independent_ai_review"
    for case_id, record in archive["cases"].items():
        assert record["input_sha256"] == historical["cases"][case_id]["input_sha256"]
        for criterion, label in record["labels"].items():
            assert set(label["reviewer_assessments"]) == {"A", "B"}
            assert (
                label["previous_adjudicated"]
                == historical["cases"][case_id]["labels"][criterion]["adjudicated"]
            )
            if label["adjudicated"] != label["oracle"]:
                assert label["contested"] and label["dissent"] and label["rationale"]


@pytest.mark.parametrize(
    "mutation", ["missing_case", "raw_label", "evidence", "historical_hash", "previous_label"]
)
def test_legacy_review_rejects_incomplete_stale_or_inconsistent_records(tmp_path, mutation):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    path = root / "independent-review/legacy-reviews.json"
    archive = json.loads(path.read_text())
    if mutation == "missing_case":
        archive["cases"].pop("pr-001")
    elif mutation == "raw_label":
        archive["cases"]["pr-001"]["labels"]["title-format"]["reviewer_assessments"]["A"][
            "label"
        ] = "failed"
    elif mutation == "historical_hash":
        archive["historical_review_sha256"] = "0" * 64
    elif mutation == "previous_label":
        archive["cases"]["pr-001"]["labels"]["title-format"]["previous_adjudicated"] = "failed"
    else:
        evidence = root / "prs/pr-001/evidence.json"
        data = json.loads(evidence.read_text())
        data["body"] += "\nAdditional context not supplied to the reviewers.\n"
        evidence.write_text(json.dumps(data))
    path.write_text(json.dumps(archive))
    index = json.loads((root / "index.json").read_text())
    selected = {
        **index,
        "cases": [case for case in index["cases"] if case["split"] == "development"],
    }
    validate = runpy.run_path(str(ROOT / "validate.py"))["validate_independent_review"]
    with pytest.raises(
        ValueError, match="cover exactly|blind judgment|re-review|historical archive|provenance"
    ):
        validate(root, selected, "independent-review/legacy-reviews.json")


def test_legacy_arbitration_corrects_evidence_errors_and_keeps_ambiguous_families_uncertain():
    archive = json.loads((ROOT / "independent-review/legacy-reviews.json").read_text())
    assert archive["cases"]["pr-087"]["labels"]["testing-explanation"]["adjudicated"] == "passed"
    for case_id in ("pr-120", "pr-121"):
        assert (
            archive["cases"][case_id]["labels"]["description-coverage"]["adjudicated"] == "failed"
        )
    index = json.loads((ROOT / "index.json").read_text())
    uncertain = []
    for case in index["cases"]:
        if case["split"] != "development":
            continue
        label = archive["cases"][case["case_id"]]["labels"]["change-triviality"]
        if case["family_id"] in {"cli-help", "cache-key", "token-compare"}:
            assert label["adjudicated"] == "uncertain" and label["contested"]
            uncertain.append(case["case_id"])
    assert len(uncertain) == 35


def test_benchmark_rejects_stale_grading_without_invalidating_prediction_bindings(tmp_path):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    path = root / "independent-review/legacy-reviews.json"
    archive = json.loads(path.read_text())
    archive["cases"]["pr-001"]["input_sha256"] = "0" * 64
    path.write_text(json.dumps(archive))
    namespace = runpy.run_path(str(ROOT / "benchmark.py"))
    namespace["score"].__globals__["ROOT"] = root
    corpus = json.loads((root / "corpus-development.json").read_text())
    summary = {
        "reference_labels": corpus,
        "cases": [{"case_id": case["case_id"], "rubric": {}} for case in corpus["cases"]],
    }
    with pytest.raises(ValueError, match="re-review"):
        namespace["score"](summary, "development")
    # Review revisions affect grading, not immutable prediction inputs.
    namespace["input_binding"]("development", root / "policy.toml", root / "providers.toml")


@pytest.mark.parametrize("split,count", [("development", 125), ("evaluation", 24), ("all", 149)])
def test_review_report_scores_selected_partition_and_exposes_final_adjudications(split, count):
    result = runpy.run_path(str(ROOT / "review_report.py"))["report"](split)
    assert result["split"] == split
    assert result["label_status"] == "provisional"
    assert result["adjudication_summary"]["case_count"] == count
    assert result["adjudication_summary"]["label_count"] == count * 7
    for reviewer in result["reviewers"].values():
        assert reviewer["case_count"] == count
        assert reviewer["grading_reference"]["revision"] == "original"
        assert {
            key: value["case_count"] for key, value in reviewer["by_review_confidence"].items()
        } == result["review_confidence_support"]
        assert all(
            value["criteria"]["testing-explanation"]["support"] == value["case_count"]
            for value in reviewer["by_review_confidence"].values()
        )
        assert sum(group["case_count"] for group in reviewer["by_scenario"].values()) == count
        assert (
            sum(group["case_count"] for group in reviewer["by_triviality_level"].values()) == count
        )
        assert all(value["support"] == count for value in reviewer["criteria"].values())
    assert set(result["archives_sha256"]) == (
        {"development", "evaluation"} if split == "all" else {split}
    )
    if split != "evaluation":
        assert any(
            int(item["case_id"].removeprefix("pr-")) <= 125
            for item in result["adjudication_summary"]["unresolved_disagreements"]
        )
