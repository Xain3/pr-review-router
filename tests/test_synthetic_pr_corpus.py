"""Keep benchmark fixtures compatible with real router evidence and patch contracts."""

import copy
import json
import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
CORPUS = Path(__file__).resolve().parents[1] / "examples" / "synthetic-prs"


def test_synthetic_pr_corpus_integrity():
    namespace = runpy.run_path(str(CORPUS / "validate.py"))
    summary = namespace["validate"](CORPUS)
    assert summary["case_count"] == 100
    assert summary["commit_count"] == 170
    assert summary["quality_counts"] == {"good": 10, "needs_revision": 90}
    assert summary["triviality_counts"] == {"0": 20, "1": 10, "2": 30, "3": 20, "4": 20}
    assert summary["complexity_counts"] == {"trivial": 30, "non_trivial": 70}


@pytest.mark.parametrize(
    ("number", "score", "level"),
    [
        (1, 0, "editorial"),
        (2, 1, "mechanical"),
        (4, 2, "bounded_behavior"),
        (7, 3, "substantive"),
        (9, 4, "incompatible"),
    ],
)
def test_triviality_is_based_on_actual_change_across_description_variants(number, score, level):
    patches = []
    qualities = set()
    for offset in range(0, 100, 10):
        directory = CORPUS / "prs" / f"pr-{number + offset:03d}"
        annotation = json.loads((directory / "annotations.json").read_text())
        criterion = annotation["criteria"]["change_triviality"]
        assert criterion["score"] == score
        assert criterion["level"] == level
        assert criterion["is_trivial"] == (score <= 1)
        assert "status" not in criterion
        qualities.add(annotation["expected_quality"])
        patches.append(json.loads((directory / "evidence.json").read_text())["files"])
    assert qualities == {"good", "needs_revision"}
    assert all(patch == patches[0] for patch in patches)


@pytest.fixture
def triviality_predictions():
    index = json.loads((CORPUS / "index.json").read_text())
    return {
        "schema_version": 1,
        "predictions": [
            {"case_id": case["case_id"], "score": case["triviality_score"]}
            for case in index["cases"]
        ],
    }


def test_triviality_benchmark_reports_ordinal_and_binary_errors(triviality_predictions):
    evaluate = runpy.run_path(str(CORPUS / "evaluate_triviality.py"))["evaluate"]
    perfect = evaluate(CORPUS, triviality_predictions)
    assert perfect["exact_score_accuracy"] == perfect["binary_triviality_accuracy"] == 1
    assert perfect["mean_absolute_error"] == 0
    assert perfect["confusion_matrix"]["counts"] == [
        [20, 0, 0, 0, 0],
        [0, 10, 0, 0, 0],
        [0, 0, 30, 0, 0],
        [0, 0, 0, 20, 0],
        [0, 0, 0, 0, 20],
    ]
    # A documentation correction incorrectly classified as a behavior change.
    imperfect = copy.deepcopy(triviality_predictions)
    imperfect["predictions"][0]["score"] = 2
    result = evaluate(CORPUS, imperfect)
    assert result["exact_score_accuracy"] == result["binary_triviality_accuracy"] == 0.99
    assert result["mean_absolute_error"] == 0.02
    assert result["exact_score_accuracy_by_pr_quality"] == {"good": 0.9, "needs_revision": 1}


@pytest.mark.parametrize("score", [-1, 5, True, 2.5, "2"])
def test_triviality_benchmark_rejects_invalid_scores(triviality_predictions, score):
    evaluate = runpy.run_path(str(CORPUS / "evaluate_triviality.py"))["evaluate"]
    triviality_predictions["predictions"][0]["score"] = score
    with pytest.raises(ValueError, match="integers from 0 through 4"):
        evaluate(CORPUS, triviality_predictions)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown"])
def test_triviality_benchmark_requires_all_unique_case_ids(triviality_predictions, kind):
    evaluate = runpy.run_path(str(CORPUS / "evaluate_triviality.py"))["evaluate"]
    if kind == "missing":
        triviality_predictions["predictions"].pop()
    elif kind == "duplicate":
        triviality_predictions["predictions"].append(triviality_predictions["predictions"][0])
    else:
        triviality_predictions["predictions"][0]["case_id"] = "pr-unknown"
    with pytest.raises(ValueError, match="unique|exactly all"):
        evaluate(CORPUS, triviality_predictions)
