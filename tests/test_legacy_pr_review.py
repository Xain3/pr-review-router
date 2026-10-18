"""Preserve fresh blind review and conservative adjudication of the original corpus."""

import hashlib
import json
import runpy
import shutil
from pathlib import Path

import pytest

from pr_review_router.corpus_references import REGISTRY

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1] / "examples" / "synthetic-prs"


@pytest.mark.parametrize(
    "filename,digest",
    [
        ("reviews.json", "8811b7d292712a04349285f3b8809264e280fda89e55a78b4af6c08f4b1fd0b2"),
        ("legacy-reviews.json", "d69bfd0bb58b622b2f9c8ddb5a800ff0d225818e2577c50b565447612582696a"),
        (
            "authored-reviews.json",
            "35f3254ccff704f2bf6799fb530abe6c0733aa2106097cbeaaf5b0f2998d3db2",
        ),
    ],
)
def test_frozen_review_archives_remain_byte_identical(filename, digest):
    assert (
        hashlib.sha256((ROOT / "independent-review" / filename).read_bytes()).hexdigest() == digest
    )


def test_expanded_development_archive_preserves_old_records_and_actual_new_judgments():
    validation = runpy.run_path(str(ROOT / "validate.py"))
    registry = json.loads((ROOT / REGISTRY).read_text())
    revision = registry["revisions"]["review-2026-10-18"]
    active = json.loads((ROOT / revision["archives"]["development"]).read_text())
    legacy = json.loads((ROOT / "independent-review/legacy-reviews.json").read_text())
    assert len(active["cases"]) == 133
    assert {case_id: active["cases"][case_id] for case_id in legacy["cases"]} == legacy["cases"]
    assert len(validation["load_grading_references"](ROOT, "all", "review-2026-10-18").cases) == 157
    expected_levels = [
        "editorial",
        "mechanical",
        "mechanical",
        "bounded_behavior",
        "bounded_behavior",
        "substantive",
        "substantive",
        "incompatible",
    ]
    expected_coverage = [
        "uncertain",
        "passed",
        "uncertain",
        "passed",
        "failed",
        "failed",
        "uncertain",
        "passed",
    ]
    for number, level, coverage in zip(
        range(150, 158), expected_levels, expected_coverage, strict=True
    ):
        case_id = f"pr-{number}"
        labels = active["cases"][case_id]["labels"]
        assert labels["change-triviality"]["adjudicated"] == level
        assert labels["review-coverage"]["adjudicated"] == coverage
        assert (
            level
            == legacy["cases"][f"pr-{number - 32}"]["labels"]["change-triviality"]["adjudicated"]
        )
        for label in labels.values():
            assert "previous_adjudicated" not in label
            for assessment in label["reviewer_assessments"].values():
                assert assessment["raw_judgment"]["value"] == assessment["label"]
                assert assessment["raw_judgment"]["explanation"] == assessment["rationale"]
                assert isinstance(assessment["raw_judgment"]["sources"], list)
        if number in {152, 153}:
            label = labels["description-coverage"]
            assert label["adjudicated"] == "failed" and label["oracle"] == "passed"
            assert label["contested"] and label["dissent"]
    reservation = active["cases"]["pr-153"]["labels"]["change-triviality"]
    assert reservation["contested"] and "pr-121" in reservation["rationale"]


@pytest.mark.parametrize(
    "mutation", ["invented_history", "missing_case", "raw_label", "copied_record"]
)
def test_expanded_review_rejects_fabricated_or_incomplete_provenance(tmp_path, mutation):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    validation = runpy.run_path(str(root / "validate.py"))
    registry = json.loads((root / REGISTRY).read_text())
    path = root / registry["revisions"]["review-2026-10-18"]["archives"]["development"]
    archive = json.loads(path.read_text())
    label = archive["cases"]["pr-150"]["labels"]["title-format"]
    if mutation == "invented_history":
        label["previous_adjudicated"] = "passed"
    elif mutation == "missing_case":
        archive["cases"].pop("pr-157")
    elif mutation == "raw_label":
        label["reviewer_assessments"]["A"]["label"] = "failed"
    else:
        archive["cases"]["pr-001"]["labels"]["title-format"]["rationale"] = (
            "Changed old assessment."
        )
    path.write_text(json.dumps(archive))
    with pytest.raises(
        ValueError,
        match="historical adjudication|incomplete reference archive|blind judgment|Preserved",
    ):
        validation["load_grading_references"](root, "development", "review-2026-10-18")


def test_fresh_legacy_review_covers_every_original_case_and_criterion():
    archive = json.loads((ROOT / "independent-review/legacy-reviews.json").read_text())
    historical = json.loads((ROOT / "independent-review/reviews.json").read_text())
    index = json.loads((ROOT / "index.json").read_text())
    registry = json.loads((ROOT / REGISTRY).read_text())
    validation = runpy.run_path(str(ROOT / "validate.py"))
    selected = {
        **index,
        "cases": [
            case for case in index["cases"] if int(case["case_id"].removeprefix("pr-")) <= 125
        ],
    }
    counts = validation["validate_independent_review"](
        ROOT,
        selected,
        "independent-review/legacy-reviews.json",
        expected_rubric_sha256=registry["revisions"]["review-2026-10-17"]["rubric_sha256"],
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
    "mutation",
    [
        "missing_case",
        "raw_label",
        "evidence",
        "historical_hash",
        "missing_historical_hash",
        "previous_label",
    ],
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
    elif mutation == "missing_historical_hash":
        archive.pop("historical_review_sha256")
    elif mutation == "previous_label":
        archive["cases"]["pr-001"]["labels"]["title-format"]["previous_adjudicated"] = "failed"
    else:
        evidence = root / "prs/pr-001/evidence.json"
        data = json.loads(evidence.read_text())
        data["body"] += "\nAdditional context not supplied to the reviewers.\n"
        evidence.write_text(json.dumps(data))
    path.write_text(json.dumps(archive))
    index = json.loads((root / "index.json").read_text())
    registry = json.loads((root / REGISTRY).read_text())
    selected = {
        **index,
        "cases": [
            case for case in index["cases"] if int(case["case_id"].removeprefix("pr-")) <= 125
        ],
    }
    validate = runpy.run_path(str(ROOT / "validate.py"))["validate_independent_review"]
    with pytest.raises(
        ValueError,
        match="cover exactly|blind judgment|re-review|historical archive|provenance",
    ):
        validate(
            root,
            selected,
            "independent-review/legacy-reviews.json",
            expected_rubric_sha256=registry["revisions"]["review-2026-10-17"]["rubric_sha256"],
        )


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
        if int(case["case_id"].removeprefix("pr-")) > 125:
            continue
        label = archive["cases"][case["case_id"]]["labels"]["change-triviality"]
        if case["family_id"] in {"cli-help", "cache-key", "token-compare"}:
            assert label["adjudicated"] == "uncertain" and label["contested"]
            uncertain.append(case["case_id"])
    assert len(uncertain) == 35


@pytest.mark.parametrize("revision", ["review-2026-10-17", "review-2026-10-18", "latest"])
def test_benchmark_rejects_stale_grading_without_invalidating_prediction_bindings(
    tmp_path, revision
):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    registry = json.loads((root / REGISTRY).read_text())
    resolved = registry["latest"] if revision == "latest" else revision
    path = root / registry["revisions"][resolved]["archives"]["development"]
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
        namespace["score"](summary, "development", revision)
    # Review revisions affect grading, not immutable prediction inputs.
    namespace["input_binding"]("development", root / "policy.toml", root / "providers.toml")


@pytest.mark.parametrize("split,count", [("development", 133), ("evaluation", 24), ("all", 157)])
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
