"""Keep benchmark fixtures compatible with real router evidence and patch contracts."""

import copy
import json
import runpy
import shutil
from pathlib import Path

import pytest

from pr_review_router.assessment_rubric import load_assessment_rubric
from pr_review_router.experiments import load_corpus, load_corpus_rubric

pytestmark = pytest.mark.unit
CORPUS = Path(__file__).resolve().parents[1] / "examples" / "synthetic-prs"


def test_synthetic_pr_corpus_integrity():
    namespace = runpy.run_path(str(CORPUS / "validate.py"))
    summary = namespace["validate"](CORPUS)
    assert summary["case_count"] == 125
    assert summary["commit_count"] == 215
    assert summary["quality_counts"] == {"good": 17, "needs_revision": 108}
    assert summary["triviality_counts"] == {"0": 23, "1": 13, "2": 38, "3": 26, "4": 25}
    assert summary["complexity_counts"] == {"trivial": 36, "non_trivial": 89}
    assert summary["text_diff_triviality_counts"] == {
        "aligned": 112,
        "conflicting_text": 8,
        "text_overstates": 1,
        "text_understates": 3,
        "not_assessable": 1,
    }
    assert summary["potential_triviality_confounders"] == 12
    assert summary["review_coverage_counts"] == {
        "not_assessable": 42,
        "passed": 43,
        "failed": 40,
    }


def test_synthetic_pr_corpus_regenerates_deterministically(tmp_path):
    namespace = runpy.run_path(str(CORPUS / "generate.py"))
    namespace["main"](tmp_path)

    generated_files = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    expected_paths = {
        path.relative_to(CORPUS)
        for path in CORPUS.rglob("*.json")
        if "independent-review" not in path.parts
    }
    expected_paths |= {path.relative_to(CORPUS) for path in CORPUS.rglob("review.md")}
    expected_paths.add(Path("INDEX.md"))

    # Three JSON files and an optional review per PR, plus the corpus and index.
    assert len(generated_files) == 125 * 3 + 103 + 3
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
    assert len(annotation["criteria"]) == 20
    assert "text_diff_triviality" not in annotation["criteria"]


def test_all_active_rubric_criteria_have_oracle_labels():
    """Keep active rubric identifiers mirrored in every synthetic oracle annotation."""
    rubric = load_assessment_rubric(CORPUS / "rubric.toml")
    rubric_ids = {criterion.criterion_id for criterion in rubric.criteria}
    index = json.loads((CORPUS / "index.json").read_text())

    assert rubric_ids <= set(index["criteria"])
    for case in index["cases"]:
        annotation = json.loads((CORPUS / case["annotations"]).read_text())
        assert rubric_ids <= set(annotation["criteria"])


def test_router_corpus_derives_labels_from_oracle_and_rubric_policy():
    """Load the generated corpus through the router's own evaluation contracts."""
    corpus, evidence, _ = load_corpus(CORPUS / "corpus.json")
    rubric, reviews, _ = load_corpus_rubric(CORPUS / "corpus.json", corpus)
    assert rubric is not None
    assert len(evidence) == 125
    # Cases with no review keep enforced review coverage uncertain; the rest supply review text.
    assert reviews.count("") == 22
    assert all(text.strip() for text in reviews if text)
    first = corpus.cases[0].expectations.rubric
    assert first is not None
    assert first.criteria["review-coverage"] == "uncertain"
    assert first.recommendation == "needs_human_review"
    assert {
        case.expectations.rubric.recommendation for case in corpus.cases if case.expectations.rubric
    } == {"block", "needs_human_review", "suggest_changes", "skip_and_approve"}


def test_benchmark_scores_oracle_identical_answers_as_full_agreement():
    """Score a summary whose router outputs equal the oracle-derived labels."""
    score = runpy.run_path(str(CORPUS / "benchmark.py"))["score"]
    corpus = json.loads((CORPUS / "corpus.json").read_text())
    index = {
        case["case_id"]: case for case in json.loads((CORPUS / "index.json").read_text())["cases"]
    }
    summary = {
        "reference_labels": corpus,
        "totals": {"unsafe_rubric_approvals": 0},
        "cases": [
            {
                "case_id": case["case_id"],
                "rubric": {
                    "recommendation": case["expectations"]["rubric"]["recommendation"],
                    "criteria": case["expectations"]["rubric"]["criteria"],
                    "native_answers": {
                        "change-triviality": {"choice": index[case["case_id"]]["triviality_level"]}
                    },
                },
            }
            for case in corpus["cases"]
        ],
    }
    result = score(summary)
    assert result["recommendation"]["agreement"] == 1
    assert all(item["agreement"] == 1 for item in result["criteria"].values())
    assert result["triviality"]["exact_accuracy"] == result["triviality"]["binary_accuracy"] == 1


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
        (3, 0, "editorial"),
        (4, 2, "bounded_behavior"),
        (5, 2, "bounded_behavior"),
        (6, 2, "bounded_behavior"),
        (7, 3, "substantive"),
        (8, 3, "substantive"),
        (9, 4, "incompatible"),
        (10, 4, "incompatible"),
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
        [23, 0, 0, 0, 0],
        [0, 13, 0, 0, 0],
        [0, 0, 38, 0, 0],
        [0, 0, 0, 26, 0],
        [0, 0, 0, 0, 25],
    ]
    # A documentation correction incorrectly classified as a behavior change.
    imperfect = copy.deepcopy(triviality_predictions)
    imperfect["predictions"][0]["score"] = 2
    result = evaluate(CORPUS, imperfect)
    assert result["exact_score_accuracy"] == result["binary_triviality_accuracy"] == 124 / 125
    assert result["mean_absolute_error"] == 2 / 125
    assert result["exact_score_accuracy_by_pr_quality"] == {"good": 16 / 17, "needs_revision": 1}


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


def _annotation(number):
    return json.loads((CORPUS / "prs" / f"pr-{number:03d}" / "annotations.json").read_text())


def test_review_variants_cover_every_coverage_outcome_and_route():
    """Review text lets the benchmark reach every recommendation, not only block/handoff."""
    corpus = json.loads((CORPUS / "corpus.json").read_text())
    reviewed = {case["case_id"]: case for case in corpus["cases"]}
    by_variant = {}
    for number in range(1, 126):
        annotation = _annotation(number)
        by_variant.setdefault(annotation["review_variant"], []).append(annotation)
        review = annotation["criteria"]["review-coverage"]
        path = CORPUS / "prs" / f"pr-{number:03d}" / "review.md"
        assert path.exists() == (annotation["review_variant"] != "none")
        # Review quality never changes the PR-text quality label.
        assert (annotation["expected_quality"] == "needs_revision") == any(
            item.get("status") == "failed"
            for key, item in annotation["criteria"].items()
            if key != "review-coverage"
        )
        assert review["status"] in {"passed", "failed", "not_assessable"}
    assert {
        variant: {item["criteria"]["review-coverage"]["status"] for item in items}
        for variant, items in by_variant.items()
    } == {
        "none": {"not_assessable"},
        "ambiguous": {"not_assessable"},
        "complete": {"passed"},
        "open_concern": {"passed"},
        "partial": {"failed"},
        "off_target": {"failed"},
        "generic": {"failed"},
    }
    recommendations = {
        case["expectations"]["rubric"]["recommendation"] for case in reviewed.values()
    }
    assert {"skip_and_approve", "suggest_changes"} <= recommendations
    # Every variant that avoids a blocker is exercised on at least one family of each depth.
    assert all(len(items) >= 5 for items in by_variant.values())


def test_review_variants_differ_in_what_they_address():
    """Keep partial, generic, and unresolvable reviews distinguishable from complete ones."""
    texts = {}
    for number in range(1, 126):
        annotation = _annotation(number)
        path = CORPUS / "prs" / f"pr-{number:03d}" / "review.md"
        if path.exists():
            files = [
                file["path"]
                for file in json.loads((path.parent / "evidence.json").read_text())["files"]
            ]
            texts.setdefault(annotation["review_variant"], []).append((path.read_text(), files))
    assert all(all(name in text for name in files) for text, files in texts["complete"]), (
        "complete reviews name every changed file"
    )
    assert all(
        files[0] in text and not any(name in text for name in files[1:])
        for text, files in texts["partial"]
    )
    assert all(
        not any(name in text for name in files)
        for text, files in texts["generic"] + texts["ambiguous"]
    )
    assert all(text.startswith("Reviewed:") for text, _ in texts["generic"])
    assert all("Open concern:" in text for text, _ in texts["open_concern"])
    assert all(not any(name in text for name in files) for text, files in texts["off_target"])


def test_contrastive_cases_reuse_base_patches_with_different_text_defects():
    """Same diff, three outcomes: concrete why, restated what, and a partial change account."""
    # pr-004 (empty-average) is the good case; pr-104 restates; pr-111 omits the test change.
    base, restated, partial = (_annotation(number) for number in (4, 104, 111))
    patches = [
        json.loads((CORPUS / "prs" / f"pr-{number:03d}" / "evidence.json").read_text())["files"]
        for number in (4, 104, 111)
    ]
    assert patches[0] == patches[1] == patches[2]
    assert base["criteria"]["rationale"]["status"] == "passed"
    assert restated["criteria"]["rationale"]["status"] == "failed"
    assert restated["criteria"]["description_diff_consistency"]["status"] == "passed"
    assert partial["criteria"]["rationale"]["status"] == "passed"
    assert partial["criteria"]["description_diff_consistency"]["status"] == "failed"
    body = json.loads((CORPUS / "prs" / "pr-104" / "evidence.json").read_text())["body"]
    rationale = body.split("## Rationale\n")[1].split("\n\n")[0]
    assert rationale.startswith("This PR does the following:")
    # A restated rationale is a different defect from the existing vague one.
    assert "improves the project" not in rationale


def test_boundary_pairs_are_adjacent_levels_with_neutral_text():
    pairs = [
        ("comment-typo", "rename-local"),
        ("extract-constant", "change-default"),
        ("strip-input", "escape-output"),
        ("optional-param", "required-param"),
    ]
    index = json.loads((CORPUS / "index.json").read_text())
    levels = {
        case["family_id"]: case["triviality_score"]
        for case in index["cases"]
        if case["scenario"] == "boundary"
    }
    assert [(levels[low], levels[high]) for low, high in pairs] == [(0, 1), (1, 2), (2, 3), (3, 4)]
    for case in index["cases"]:
        if case["scenario"] == "boundary":
            evidence = json.loads((CORPUS / case["evidence"]).read_text())
            assert evidence["title"].startswith("chore(")
    # The incompatible change is not disclosed, so only the diff supports level 4.
    required = next(case for case in index["cases"] if case["family_id"] == "required-param")
    annotation = json.loads((CORPUS / required["annotations"]).read_text())
    assert annotation["criteria"]["breaking_changes"]["status"] == "failed"
    assert annotation["diagnostics"]["text_diff_triviality"]["relationship"] == "text_understates"


def test_rubric_instructions_separate_failure_from_insufficient_evidence():
    rubric = load_assessment_rubric(CORPUS / "rubric.toml")
    instructions = {
        criterion.criterion_id: criterion.instructions or "" for criterion in rubric.criteria
    }
    assert "says why" in instructions["change-rationale"]
    assert "uncertain, not passed" in instructions["description-coverage"]
    assert "Choose failed" in instructions["description-coverage"]
    assert "Choose uncertain when no review is supplied" in instructions["review-coverage"]
    assert "new required parameter" in instructions["change-triviality"]


def _reviews():
    return json.loads((CORPUS / "independent-review" / "reviews.json").read_text())


def test_independent_review_covers_every_case_without_claiming_human_review():
    reviews = _reviews()
    index = json.loads((CORPUS / "index.json").read_text())
    corpus = json.loads((CORPUS / "corpus.json").read_text())
    assert reviews["label_status"] == "independent_ai_review"
    # The router's own corpus must keep saying that no human has reviewed the labels.
    assert corpus["label_status"] == index["label_status"] == "provisional"
    assert set(reviews["cases"]) == {case["case_id"] for case in index["cases"]}
    assert all(len(record["labels"]) == 7 for record in reviews["cases"].values())
    # Every split is recorded with the dissenting rationale rather than silently resolved.
    for record in reviews["cases"].values():
        for label in record["labels"].values():
            if label["resolution"] != "unanimous":
                assert label["contested"] and label["dissent"]


def test_independent_review_labels_agree_with_the_oracle_except_recorded_splits():
    reviews = _reviews()
    splits = {
        (case_id, criterion): label
        for case_id, record in reviews["cases"].items()
        for criterion, label in record["labels"].items()
        if label["resolution"] != "unanimous"
    }
    assert set(splits) == {("pr-087", "testing-explanation")}
    assert splits[("pr-087", "testing-explanation")]["reviewers"] == {"A": "passed", "B": "failed"}
    # Review coverage drives routing, so its labels must match the oracle everywhere.
    assert all(
        record["labels"]["review-coverage"]["adjudicated"]
        == record["labels"]["review-coverage"]["oracle"]
        for record in reviews["cases"].values()
    )


@pytest.mark.parametrize("change", ["evidence", "rubric", "claim"])
def test_validator_rejects_stale_or_overclaiming_independent_review(tmp_path, change):
    shutil.copytree(CORPUS, tmp_path / "corpus", ignore=shutil.ignore_patterns("__pycache__"))
    root = tmp_path / "corpus"
    if change == "evidence":
        path = root / "prs" / "pr-001" / "evidence.json"
        path.write_text(path.read_text().replace("Read the guide", "Read this guide"))
    elif change == "rubric":
        (root / "rubric.toml").write_text((root / "rubric.toml").read_text() + "\n# edited\n")
    else:
        path = root / "independent-review" / "reviews.json"
        path.write_text(path.read_text().replace("independent_ai_review", "human_reviewed", 1))
    validate = runpy.run_path(str(CORPUS / "validate.py"))["validate"]
    with pytest.raises(ValueError, match="re-review|human review"):
        validate(root)
