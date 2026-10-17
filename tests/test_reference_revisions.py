"""Grade unchanged predictions against versioned judgments without inventing certainty."""

import copy
import hashlib
import json
import runpy
import shutil
from pathlib import Path

import pytest

from pr_review_router.corpus_references import REGISTRY, load_reference_revision

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1] / "examples" / "synthetic-prs"


def _summary(root=ROOT, split="all", answers="original"):
    """Construct isolated accounting predictions with an independently pinned run origin.

    :param root: Fixture root.
    :param split: Partition covered by the synthetic predictions.
    :param answers: Revision whose judgments are used to exercise grading.
    :returns: Synthetic summary, without calling any provider.
    """
    corpus = json.loads(
        (root / ("corpus.json" if split == "all" else f"corpus-{split}.json")).read_text()
    )
    references = load_reference_revision(root, split, answers)
    return {
        "mode": "synthetic_accounting",
        "label_status": "provisional",
        "reference_labels": corpus,
        "totals": {"schema_failures": 0, "transport_failures": 0, "rubric_schema_failures": 0},
        "cases": [
            {
                "case_id": case["case_id"],
                "rubric": {
                    "criteria": copy.deepcopy(references.cases[case["case_id"]]["criteria"]),
                    "recommendation": references.cases[case["case_id"]]["recommendation"],
                    "native_answers": {
                        "change-triviality": {
                            "choice": references.cases[case["case_id"]]["triviality_level"],
                        }
                    },
                },
            }
            for case in corpus["cases"]
        ],
    }


def test_latest_defaults_use_adjudications_and_preserve_unknown_reference_levels():
    summary = _summary()
    before = copy.deepcopy(summary)
    score = runpy.run_path(str(ROOT / "benchmark.py"))["score"]
    latest = score(summary)
    original = score(summary, reference_revision="original")
    assert summary == before
    assert original["recommendation"]["agreement"] == original["triviality"]["exact_accuracy"] == 1
    assert latest["grading_reference"]["revision"] == "review-2026-10-18"
    assert latest["grading_references"]["pr-087"]["criteria"]["testing-explanation"] == "passed"
    assert latest["grading_references"]["pr-120"]["criteria"]["description-coverage"] == "failed"
    assert latest["grading_references"]["pr-121"]["recommendation"] == "block"
    assert latest["triviality"]["reference_uncertain"] == 41
    assert latest["triviality"]["reference_classified"] == 116
    assert latest["triviality"]["unresolved_reference_classifications"] == 41
    assert latest["by_triviality_level"]["uncertain"]["case_count"] == 41
    for reference in latest["grading_references"].values():
        if reference["triviality_score"] is None:
            assert reference["criteria"]["change-triviality"] == "uncertain"
            assert reference["recommendation"] in {"block", "needs_human_review"}
    for dimension in ("scenario", "triviality_level", "family_id", "split"):
        assert sum(group["case_count"] for group in latest[f"by_{dimension}"].values()) == 157


def test_latest_perfect_answers_grade_uncertainty_separately_from_numeric_accuracy():
    score = runpy.run_path(str(ROOT / "benchmark.py"))["score"]
    result = score(_summary(answers="latest"))
    assert result["recommendation"]["agreement"] == 1
    assert all(value["agreement"] == 1 for value in result["criteria"].values())
    assert result["triviality"]["native_label_agreement"] == 1
    assert result["triviality"]["paired_classified"] == 116
    assert result["triviality"]["confusion_matrix"]["uncertain_reference"]["uncertain"] == 41
    unknown = result["by_triviality_level"]["uncertain"]["triviality"]
    assert unknown["exact_accuracy"] is unknown["binary_accuracy"] is None
    assert unknown["mean_absolute_error_when_classified"] is None
    assert unknown["support"] == 41


@pytest.mark.parametrize(
    "revision,split",
    [("historical-125", "evaluation"), ("historical-125", "all"), ("unknown", "all")],
)
def test_incomplete_or_unknown_revision_fails_explicitly(revision, split):
    with pytest.raises(ValueError, match="does not cover|Unknown reference revision"):
        load_reference_revision(ROOT, split, revision)


def test_historical_and_original_sources_are_explicit_and_do_not_rewrite_cases():
    historical = load_reference_revision(ROOT, "development", "historical-125")
    latest = load_reference_revision(ROOT, "development")
    assert historical.cases["pr-087"]["criteria"]["testing-explanation"] == "failed"
    assert latest.cases["pr-087"]["criteria"]["testing-explanation"] == "passed"
    assert historical.cases["pr-002"]["triviality_score"] == 1
    assert latest.cases["pr-002"]["triviality_score"] is None
    assert len(historical.cases) == 125
    annotation = json.loads((ROOT / "prs/pr-087/annotations.json").read_text())
    assert annotation["criteria"]["testing-explanation"]["status"] == "failed"


@pytest.mark.parametrize(
    "revision,split,count",
    [
        ("historical-125", "development", 125),
        ("review-2026-10-17", "development", 125),
        ("review-2026-10-17", "evaluation", 24),
        ("review-2026-10-17", "all", 149),
        ("review-2026-10-18", "development", 133),
        ("latest", "all", 157),
    ],
)
def test_registered_review_scopes_are_explicit_and_fully_validated(revision, split, count):
    validation = runpy.run_path(str(ROOT / "validate.py"))
    references = validation["load_grading_references"](ROOT, split, revision)
    assert len(references.cases) == count
    if revision in {"historical-125", "review-2026-10-17"}:
        assert not set(references.cases) & {f"pr-{number}" for number in range(150, 158)}
        assert len(references.metadata["case_ids"]["development"]) == 125


@pytest.mark.parametrize("revision", ["historical-125", "review-2026-10-17"])
def test_frozen_scopes_never_invent_references_for_explicitly_selected_new_cases(revision):
    references = load_reference_revision(
        ROOT, "development", revision, selected_case_ids=["pr-118", "pr-125"]
    )
    assert set(references.cases) == {"pr-118", "pr-125"}
    with pytest.raises(ValueError, match="does not cover development: pr-150"):
        load_reference_revision(
            ROOT, "development", revision, selected_case_ids=["pr-118", "pr-150"]
        )


@pytest.mark.parametrize(
    "corruption",
    ["duplicate", "unknown", "wrong_partition", "missing", "unscoped", "latest_subset"],
)
def test_registered_scope_cannot_hide_missing_or_invalid_review_coverage(tmp_path, corruption):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    registry = json.loads((root / REGISTRY).read_text())
    definition = registry["revisions"]["review-2026-10-17"]
    scope = definition["case_ids"]["development"]
    if corruption == "duplicate":
        scope.append(scope[0])
    elif corruption == "unknown":
        scope.append("pr-999")
    elif corruption == "wrong_partition":
        scope.append("pr-126")
    elif corruption == "missing":
        scope.remove("pr-001")
    elif corruption == "unscoped":
        del definition["case_ids"]
    else:
        registry["latest"] = "review-2026-10-17"
    (root / REGISTRY).write_text(json.dumps(registry))
    with pytest.raises(ValueError, match="Invalid reference scope|incomplete reference archive"):
        load_reference_revision(root, "development", "review-2026-10-17")


@pytest.fixture
def bound_predictions(tmp_path):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    namespace = runpy.run_path(str(ROOT / "benchmark.py"))
    namespace["score"].__globals__["ROOT"] = root
    summary = _summary(root, "evaluation")
    path = tmp_path / "predictions" / "summary.json"
    path.parent.mkdir()
    path.write_text(json.dumps(summary))
    binding = namespace["input_binding"](
        "evaluation", root / "policy.toml", root / "providers.toml"
    )
    namespace["write_binding"](path, binding)
    return root, namespace, path, binding


def test_offline_cli_rescores_same_predictions_with_latest_and_original(
    bound_predictions, monkeypatch, tmp_path
):
    root, namespace, path, _ = bound_predictions
    original_bytes = path.read_bytes()

    def no_inference(*args, **kwargs):
        raise AssertionError("offline rescoring called inference")

    monkeypatch.setitem(namespace["main"].__globals__, "run_router", no_inference)
    results = {}
    for revision in ("original", "latest"):
        output = tmp_path / revision
        monkeypatch.setattr(
            namespace["sys"],
            "argv",
            [
                "benchmark.py",
                "--summary",
                str(path),
                "--reference-revision",
                revision,
                "--output-dir",
                str(output),
                "--config",
                str(root / "policy.toml"),
                "--providers-config",
                str(root / "providers.toml"),
            ],
        )
        namespace["main"]()
        results[revision] = json.loads((output / "benchmark.json").read_text())
    assert path.read_bytes() == original_bytes
    assert results["original"]["recommendation"]["agreement"] == 1
    assert results["latest"]["triviality"]["reference_uncertain"] == 6
    assert (
        results["original"]["prediction_summary_sha256"]
        == results["latest"]["prediction_summary_sha256"]
    )


def test_new_review_revision_changes_grading_without_invalidating_predictions(bound_predictions):
    root, namespace, path, binding = bound_predictions
    archive_path = root / "independent-review/authored-reviews.json"
    archive = json.loads(archive_path.read_text())
    label = archive["cases"]["pr-126"]["labels"]["testing-explanation"]
    label.update(
        adjudicated="uncertain",
        contested=True,
        resolution="oracle_overruled",
        dissent="Further evidence is needed to settle validation coverage.",
    )
    for key in archive["criteria"]:
        labels = [record["labels"][key] for record in archive["cases"].values()]
        archive["summary"][key] = {
            "unanimous": sum(item["resolution"] == "unanimous" for item in labels),
            "split": sum(item["resolution"] != "unanimous" for item in labels),
            "contested": sum(item["contested"] for item in labels),
        }
    relative = "independent-review/future-review.json"
    (root / relative).write_text(json.dumps(archive))
    registry = json.loads((root / REGISTRY).read_text())
    registry["revisions"]["future-review"] = {
        "kind": "adjudicated_review",
        "review_protocol": "blind_assessments",
        "archives": {
            "development": "independent-review/development-reviews-2026-10-18.json",
            "evaluation": relative,
        },
    }
    registry["latest"] = "future-review"
    (root / REGISTRY).write_text(json.dumps(registry))
    current = namespace["input_binding"](
        "evaluation", root / "policy.toml", root / "providers.toml"
    )
    assert current == binding
    origins = namespace["verify_binding"](path, current)
    scored = namespace["score"](
        json.loads(path.read_text()), "evaluation", prediction_references=origins
    )
    assert scored["grading_reference"]["revision"] == "future-review"
    assert scored["grading_references"]["pr-126"]["criteria"]["testing-explanation"] == "uncertain"
    assert scored["grading_references"]["pr-126"]["recommendation"] == "needs_human_review"


@pytest.mark.parametrize(
    "role", ["evidence", "metadata", "review", "rubric", "policy", "providers", "split", "summary"]
)
def test_reference_selection_never_bypasses_changed_prediction_inputs(bound_predictions, role):
    root, namespace, path, _ = bound_predictions
    files = {
        "evidence": root / "prs/pr-126/evidence.json",
        "metadata": root / "prs/pr-126/metadata.json",
        "review": root / "prs/pr-126/review.md",
        "rubric": root / "rubric.toml",
        "policy": root / "policy.toml",
        "providers": root / "providers.toml",
        "split": root / "splits.json",
        "summary": path,
    }
    target = files[role]
    if role == "split":
        data = json.loads(target.read_text())
        data["protocol"] += " Updated provenance."
        target.write_text(json.dumps(data))
    else:
        target.write_text(target.read_text() + "\n")
    expected = namespace["input_binding"](
        "evaluation", root / "policy.toml", root / "providers.toml"
    )
    with pytest.raises(ValueError, match="Stale"):
        namespace["verify_binding"](path, expected)


def test_legacy_bindings_allow_new_review_grading_when_original_labels_are_verifiable(
    bound_predictions,
):
    root, namespace, path, binding = bound_predictions
    legacy = copy.deepcopy(binding)
    legacy["schema_version"] = 1
    legacy.pop("prediction_reference_labels")
    legacy["inputs_sha256"]["corpus.json"] = hashlib.sha256(
        (root / "corpus.json").read_bytes()
    ).hexdigest()
    legacy["inputs_sha256"]["independent-review/authored-reviews.json"] = (
        "old-label-only-fingerprint"
    )
    namespace["write_binding"](path, legacy)
    origins = namespace["verify_binding"](path, binding)
    result = namespace["score"](
        json.loads(path.read_text()), "evaluation", prediction_references=origins
    )
    assert result["triviality"]["reference_uncertain"] == 6


def test_standalone_triviality_defaults_to_selected_adjudications():
    predictions = {
        "schema_version": 1,
        "predictions": [
            {"case_id": case_id, "score": case["triviality_score"]}
            for case_id, case in load_reference_revision(ROOT).cases.items()
        ],
    }
    evaluate = runpy.run_path(str(ROOT / "evaluate_triviality.py"))["evaluate"]
    result = evaluate(ROOT, predictions)
    assert result["reference_uncertain"] == 41
    assert result["native_label_agreement"] == 1
    assert result["exact_score_accuracy"] == 1
    assert result["by_triviality_level"]["uncertain"]["support"] == 41


def test_revision_archive_paths_cannot_leave_the_corpus(tmp_path):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    registry = json.loads((root / REGISTRY).read_text())
    registry["revisions"]["bad"] = {
        "kind": "adjudicated_review",
        "review_protocol": "blind_assessments",
        "archives": {"development": "../outside.json"},
    }
    (root / REGISTRY).write_text(json.dumps(registry))
    with pytest.raises(ValueError, match="Unsafe reference source"):
        load_reference_revision(root, "development", "bad")


@pytest.mark.parametrize(
    "redirect", ["evidence", "review", "assessment_rubric", "category", "order"]
)
def test_binding_rejects_redirected_execution_inputs(bound_predictions, redirect):
    root, namespace, _, _ = bound_predictions
    corpus = json.loads((root / "corpus.json").read_text())
    if redirect == "assessment_rubric":
        corpus[redirect] = "alternate-rubric.toml"
    elif redirect == "order":
        corpus["cases"].reverse()
    else:
        case = next(case for case in corpus["cases"] if case["case_id"] == "pr-126")
        case[redirect] = "other" if redirect == "category" else "prs/pr-127/review.md"
    (root / "corpus.json").write_text(json.dumps(corpus))
    index = json.loads((root / "index.json").read_text())
    for split in ("development", "evaluation"):
        ids = {case["case_id"] for case in index["cases"] if case["split"] == split}
        partition = {
            **corpus,
            "cases": [case for case in corpus["cases"] if case["case_id"] in ids],
        }
        (root / f"corpus-{split}.json").write_text(json.dumps(partition))
    with pytest.raises(ValueError, match="execution rubric|Execution corpus|execution corpus"):
        namespace["input_binding"]("evaluation", root / "policy.toml", root / "providers.toml")


@pytest.mark.parametrize(
    "corruption",
    ["raw_label", "raw_evidence", "missing_raw", "family", "history", "missing_history_hash"],
)
def test_newly_named_reviews_receive_full_provenance_validation(bound_predictions, corruption):
    root, namespace, path, _ = bound_predictions
    development = corruption in {"history", "missing_history_hash"}
    source = "development-reviews-2026-10-18.json" if development else "authored-reviews.json"
    archive = json.loads((root / "independent-review" / source).read_text())
    case_id = "pr-001" if development else "pr-127" if corruption == "family" else "pr-126"
    criterion = "change-triviality" if corruption == "family" else "testing-explanation"
    label = archive["cases"][case_id]["labels"][criterion]
    if corruption == "raw_label":
        label["reviewer_assessments"]["A"]["label"] = "uncertain"
    elif corruption == "raw_evidence":
        label["reviewer_assessments"]["A"]["evidence"] = ["patch:nonexistent.py"]
    elif corruption == "missing_raw":
        del label["reviewer_assessments"]
    elif corruption == "history":
        label["previous_adjudicated"] = "uncertain"
    elif corruption == "missing_history_hash":
        archive.pop("historical_review_sha256")
    else:
        label.update(adjudicated="incompatible", contested=True, dissent="Unresolved impact.")
    relative = "independent-review/newly-named.json"
    (root / relative).write_text(json.dumps(archive))
    registry = json.loads((root / REGISTRY).read_text())
    definition = copy.deepcopy(registry["revisions"][registry["latest"]])
    definition["archives"]["development" if development else "evaluation"] = relative
    registry["revisions"]["newly-named"] = definition
    registry["latest"] = "newly-named"
    (root / REGISTRY).write_text(json.dumps(registry))
    with pytest.raises(
        ValueError,
        match="blind judgment|final triviality|historical adjudication|historical archive hash",
    ):
        namespace["score"](json.loads(path.read_text()), "evaluation")
