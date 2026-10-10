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
    assert summary["text_diff_triviality_counts"] == {
        "aligned": 88,
        "conflicting_text": 8,
        "text_overstates": 1,
        "text_understates": 2,
        "not_assessable": 1,
    }
    assert summary["potential_triviality_confounders"] == 11


def test_synthetic_pr_corpus_regenerates_deterministically(tmp_path):
    namespace = runpy.run_path(str(CORPUS / "generate.py"))
    namespace["main"](tmp_path)

    generated_files = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    expected_paths = {path.relative_to(CORPUS) for path in CORPUS.rglob("*.json")}
    expected_paths.add(Path("INDEX.md"))

    assert len(generated_files) == 302
    assert set(generated_files) == expected_paths
    assert all(
        content == (CORPUS / relative_path).read_bytes()
        for relative_path, content in generated_files.items()
    )


def test_combined_empty_average_case_title_conflicts_with_false_summary():
    evidence = json.loads((CORPUS / "prs" / "pr-094" / "evidence.json").read_text())
    annotation = json.loads((CORPUS / "prs" / "pr-094" / "annotations.json").read_text())

    assert evidence["title"] == "fix(stats): return zero for an empty average input"
    assert annotation["criteria"]["description_title_consistency"]["status"] == "failed"
    assert annotation["criteria"]["title_diff_consistency"]["status"] == "passed"


@pytest.mark.parametrize(
    ("case_id", "title_score", "body_score", "text_score", "relationship", "confounder"),
    [
        ("pr-072", 4, 4, 4, "text_overstates", True),
        ("pr-077", 3, 3, 3, "aligned", False),
        ("pr-079", 1, 1, 1, "text_understates", True),
        ("pr-080", 2, 2, 2, "text_understates", True),
        ("pr-051", 2, 0, None, "conflicting_text", True),
        ("pr-092", None, None, None, "not_assessable", None),
        ("pr-026", None, 2, 2, "aligned", False),
        ("pr-049", 4, None, 4, "aligned", False),
        ("pr-074", 2, 2, 2, "aligned", False),
        ("pr-061", 0, 0, 0, "aligned", False),
    ],
)
def test_text_diff_diagnostics_keep_mismatches_conflicts_and_unknowns(
    case_id,
    title_score,
    body_score,
    text_score,
    relationship,
    confounder,
):
    annotation = json.loads((CORPUS / "prs" / case_id / "annotations.json").read_text())
    diagnostic = annotation["diagnostics"]["text_diff_triviality"]
    assert diagnostic["title_score"] == title_score
    assert diagnostic["body_score"] == body_score
    assert diagnostic["text_score"] == text_score
    assert diagnostic["relationship"] == relationship
    assert diagnostic["potential_confounder"] is confounder
    assert diagnostic["diff_score"] == annotation["criteria"]["change_triviality"]["score"]
    assert len(annotation["criteria"]) == 13
    assert "text_diff_triviality" not in annotation["criteria"]


def test_triviality_scorer_does_not_use_diagnostic_text_scores(tmp_path, triviality_predictions):
    index = json.loads((CORPUS / "index.json").read_text())
    (tmp_path / "index.json").write_text(json.dumps(index))
    for case in index["cases"]:
        annotation = json.loads((CORPUS / case["annotations"]).read_text())
        diagnostic = annotation["diagnostics"]["text_diff_triviality"]
        # Deliberately alter only diagnostic projections; the score oracle stays unchanged.
        diagnostic["text_score"] = (annotation["criteria"]["change_triviality"]["score"] + 1) % 5
        destination = tmp_path / case["annotations"]
        destination.parent.mkdir(parents=True)
        destination.write_text(json.dumps(annotation))
    evaluate = runpy.run_path(str(CORPUS / "evaluate_triviality.py"))["evaluate"]
    assert evaluate(tmp_path, triviality_predictions) == evaluate(CORPUS, triviality_predictions)


def test_text_projections_are_independent_of_reference_diff_label():
    namespace = runpy.run_path(str(CORPUS / "generate.py"))
    family = next(item for item in namespace["FAMILIES"] if item.identifier == "api-field")
    # Simulate a reference-label edit without changing the title/body claims.
    namespace["TRIVIALITY_ASSESSMENTS"]["api-field"] = (1, "Changed reference for this test.")
    _, _, annotation = namespace["make_case"](9, family, "good", 8)
    diagnostic = annotation["diagnostics"]["text_diff_triviality"]
    assert diagnostic["diff_score"] == 1
    assert diagnostic["title_score"] == diagnostic["body_score"] == diagnostic["text_score"] == 4
    assert diagnostic["relationship"] == "text_overstates"


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
