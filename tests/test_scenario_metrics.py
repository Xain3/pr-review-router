"""Exercise family holdouts and errors that aggregate agreement can conceal."""

import copy
import json
import runpy
import shutil
from pathlib import Path

import pytest

from pr_review_router.experiments import load_corpus, load_corpus_rubric

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1] / "examples" / "synthetic-prs"


@pytest.fixture
def evaluation_summary():
    """Supply ideal predictions solely to exercise the scorer's accounting.

    :returns: Separate predictions and pinned reference labels for held-out cases.
    """
    corpus = json.loads((ROOT / "corpus-evaluation.json").read_text())
    index = {
        case["case_id"]: case for case in json.loads((ROOT / "index.json").read_text())["cases"]
    }
    return {
        "reference_labels": corpus,
        "cases": [
            {
                "case_id": case["case_id"],
                "rubric": {
                    **copy.deepcopy(case["expectations"]["rubric"]),
                    "native_answers": {
                        "change-triviality": {
                            "choice": index[case["case_id"]]["triviality_level"],
                        }
                    },
                },
            }
            for case in corpus["cases"]
        ],
    }


def _score(summary):
    """Score the evaluation partition against local references.

    :param summary: Experiment outputs with embedded references.
    :returns: Stratified benchmark metrics.
    """
    return runpy.run_path(str(ROOT / "benchmark.py"))["score"](summary, "evaluation", "original")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "references"])
def test_benchmark_rejects_incomplete_or_unpinned_results(evaluation_summary, mutation):
    if mutation == "missing":
        evaluation_summary["cases"].pop()
    elif mutation == "duplicate":
        evaluation_summary["cases"].append(evaluation_summary["cases"][0])
    elif mutation == "unknown":
        evaluation_summary["cases"][0]["case_id"] = "unknown"
    else:
        evaluation_summary["reference_labels"]["cases"][0]["expectations"]["rubric"][
            "recommendation"
        ] = "block"
        # Make the mutation independent of whichever recommendation the case originally has.
        evaluation_summary["reference_labels"]["cases"][0]["expectations"]["rubric"]["criteria"][
            "title-format"
        ] = "uncertain"
    with pytest.raises(ValueError, match="selected|pinned"):
        _score(evaluation_summary)


def test_scenario_confusion_exposes_a_concentrated_false_pass(evaluation_summary):
    target = next(
        case
        for case in evaluation_summary["cases"]
        if case["rubric"]["criteria"]["description-coverage"] == "failed"
        and case["rubric"]["recommendation"] == "block"
    )
    target["rubric"]["criteria"]["description-coverage"] = "passed"
    target["rubric"]["recommendation"] = "skip_and_approve"
    target["rubric"]["native_answers"]["change-triviality"]["choice"] = "editorial"
    result = _score(evaluation_summary)
    case = next(
        case
        for case in json.loads((ROOT / "index.json").read_text())["cases"]
        if case["case_id"] == target["case_id"]
    )
    cell = result["by_scenario_and_triviality_level"][case["scenario"]][case["triviality_level"]]
    assert cell["criteria"]["description-coverage"]["expected_to_actual"]["failed->passed"] == 1
    assert cell["criteria"]["description-coverage"]["false_passes"] == 1
    assert cell["recommendation"]["unsafe_approvals"] == 1
    assert cell["recommendation"]["missed_blocks"] == 1
    assert result["criteria"]["description-coverage"]["agreement"] > 0.9
    for dimension in ("scenario", "triviality_level", "split", "author_id", "family_id"):
        groups = result[f"by_{dimension}"]
        assert sum(group["case_count"] for group in groups.values()) == result["case_count"]
        assert (
            sum(
                group["criteria"]["description-coverage"]["false_passes"]
                for group in groups.values()
            )
            == 1
        )


def test_abstentions_and_missing_answers_remain_separate(evaluation_summary):
    first, second = evaluation_summary["cases"][:2]
    first["rubric"]["native_answers"]["change-triviality"] = {"choice": "uncertain"}
    second["rubric"] = {}
    result = _score(evaluation_summary)
    matrix = result["triviality"]["confusion_matrix"]
    assert sum(matrix["uncertain"]) == sum(matrix["unanswered"]) == 1
    assert result["triviality"]["classified"] == result["case_count"] - 2
    assert result["criteria"]["description-coverage"]["unanswered"] == 1
    assert result["triviality"]["exact_accuracy"] < 1


def test_evaluation_corpus_contains_only_complete_held_out_families():
    corpus, evidence, _ = load_corpus(ROOT / "corpus-evaluation.json")
    rubric, reviews, _ = load_corpus_rubric(ROOT / "corpus-evaluation.json", corpus)
    assert rubric is not None and len(evidence) == len(reviews) == 24
    split = json.loads((ROOT / "splits.json").read_text())["partitions"]
    assert len(split["evaluation"]["family_ids"]) == 12
    assert set(split["evaluation"]["family_ids"]).isdisjoint(split["development"]["family_ids"])
    assert [case.case_id for case in corpus.cases] == split["evaluation"]["case_ids"]


@pytest.mark.parametrize("mutation", ["family", "patch"])
def test_partition_validation_rejects_leaked_families_or_patches(tmp_path, mutation):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    index = json.loads((root / "index.json").read_text())
    first = index["cases"][0]
    heldout = next(case for case in index["cases"] if case["split"] == "evaluation")
    if mutation == "family":
        heldout["family_id"] = first["family_id"]
        splits = json.loads((root / "splits.json").read_text())
        splits["partitions"]["evaluation"]["family_ids"] = sorted(
            {case["family_id"] for case in index["cases"] if case["split"] == "evaluation"}
        )
        (root / "splits.json").write_text(json.dumps(splits))
    else:
        evidence = json.loads((root / heldout["evidence"]).read_text())
        evidence["files"] = json.loads((root / first["evidence"]).read_text())["files"]
        (root / heldout["evidence"]).write_text(json.dumps(evidence))
    validate = runpy.run_path(str(ROOT / "validate.py"))["validate_splits"]
    with pytest.raises(ValueError, match="overlaps"):
        validate(root, index)


def test_partition_validation_rejects_evidence_redirected_in_both_corpora(tmp_path):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    target_id = "pr-126"
    redirected = "prs/pr-001/evidence.json"
    for name in ("corpus.json", "corpus-evaluation.json"):
        path = root / name
        corpus = json.loads(path.read_text())
        case = next(case for case in corpus["cases"] if case["case_id"] == target_id)
        case["evidence"] = redirected
        path.write_text(json.dumps(corpus))

    validate = runpy.run_path(str(ROOT / "validate.py"))["validate_splits"]
    with pytest.raises(ValueError, match="execution corpus inputs differ from index"):
        validate(root, json.loads((root / "index.json").read_text()))


def test_triviality_scores_only_the_selected_partition_and_counts_abstention():
    index = json.loads((ROOT / "index.json").read_text())
    predictions = {
        "schema_version": 1,
        "predictions": [
            {"case_id": case["case_id"], "score": case["triviality_score"]}
            for case in index["cases"]
            if case["split"] == "evaluation"
        ],
    }
    predictions["predictions"][0]["score"] = None
    evaluate = runpy.run_path(str(ROOT / "evaluate_triviality.py"))["evaluate"]
    result = evaluate(ROOT, predictions, "evaluation", "original")
    assert result["case_count"] == 24 and result["classified"] == 23
    assert result["exact_score_accuracy"] == 23 / 24
    assert sum(result["confusion_matrix"]["uncertain"]) == 1
    assert sum(group["support"] for group in result["by_scenario"].values()) == 24

    latest = evaluate(ROOT, predictions, "evaluation", "latest")
    assert {
        group: metrics["support"] for group, metrics in latest["by_reference_confidence"].items()
    } == {"contested": 14, "uncontested_ai": 10}

    with pytest.raises(ValueError, match="selected"):
        evaluate(ROOT, predictions, "all")


def test_benchmark_executes_only_the_selected_corpus(tmp_path, monkeypatch):
    namespace = runpy.run_path(str(ROOT / "benchmark.py"))
    (tmp_path / "summary.json").write_text('{"cases": []}')
    commands = []
    monkeypatch.setattr(
        namespace["subprocess"], "run", lambda command, **kwargs: commands.append(command)
    )
    namespace["run_router"](
        tmp_path,
        config=ROOT / "policy.toml",
        providers=ROOT / "providers.toml",
        replay_dir=None,
        record=False,
    )
    command = commands[0]
    assert Path(command[command.index("--corpus") + 1]).name == "corpus-evaluation.json"
    assert json.loads((tmp_path / "benchmark-inputs.json").read_text())["split"] == "evaluation"


@pytest.mark.parametrize("size,per_family", [(30, 1), (60, 2)])
def test_random_sample_covers_families_and_reproduces_seed(size, per_family):
    sample = runpy.run_path(str(ROOT / "benchmark.py"))["sample_cases"]
    cases = json.loads((ROOT / "index.json").read_text())["cases"]
    selected = sample(size, 42)
    assert len(selected) == len(set(selected)) == size
    assert selected == sample(size, 42)
    assert selected != sample(size, 43)
    assert {case["family_id"] for case in cases if case["case_id"] in selected} == {
        case["family_id"] for case in cases
    }
    for family in {case["family_id"] for case in cases}:
        assert (
            sum(case["family_id"] == family and case["case_id"] in selected for case in cases)
            == per_family
        )
    assert len(sample(10, 42)) == 10
    assert len(sample(len(cases), 42)) == len(cases)
    for size in (0, len(cases) + 1):
        with pytest.raises(ValueError, match="Sample size"):
            sample(size, 42)


def test_random_sample_execution_corpus_loads_selected_inputs(tmp_path, monkeypatch):
    namespace = runpy.run_path(str(ROOT / "benchmark.py"))
    selected = namespace["sample_cases"](60, 42)
    (tmp_path / "summary.json").write_text('{"cases": []}')

    def check_corpus(command, **kwargs):
        path = Path(command[command.index("--corpus") + 1])
        corpus, evidence, _ = load_corpus(path)
        rubric, reviews, _ = load_corpus_rubric(path, corpus)
        assert [case.case_id for case in corpus.cases] == selected
        assert len(evidence) == len(reviews) == 60
        assert rubric is not None

    monkeypatch.setattr(namespace["subprocess"], "run", check_corpus)
    namespace["run_router"](
        tmp_path,
        config=ROOT / "policy.toml",
        providers=ROOT / "providers.toml",
        replay_dir=None,
        record=False,
        split="random",
        selected_case_ids=selected,
    )
    binding = json.loads((tmp_path / "benchmark-inputs.json").read_text())
    assert binding["selected_case_ids"] == selected


def test_model_provenance_collects_configured_and_observed_models(tmp_path):
    collect = runpy.run_path(str(ROOT / "benchmark.py"))["collect_model_provenance"]
    artifact = {
        "providers": {
            "decision": {
                "backend": "ollaya",
                "endpoint": "http://localhost:11435",
                "model": "winnow:e4b",
            },
            "review": {"backend": "mock"},
        },
        "decision": {
            "models": {
                "winnow:e4b": {
                    "digest": "sha256:abc",
                    "server_version": "1.2.3",
                    "template": "not exported",
                }
            }
        },
        "review": {"responses": []},
    }
    (tmp_path / "case-a.experiment.json").write_text(json.dumps(artifact))

    provenance = collect(tmp_path, ["case-a", "case-b"])

    assert provenance["status"] == "partial"
    assert provenance["artifacts_found"] == 1
    assert provenance["missing_case_ids"] == ["case-b"]
    assert provenance["roles"]["decision"]["selections"] == [
        {"backend": "ollaya", "configured_model": "winnow:e4b"}
    ]
    assert provenance["roles"]["decision"]["models"] == [
        {"model": "winnow:e4b", "digest": "sha256:abc", "server_version": "1.2.3"}
    ]
    assert provenance["roles"]["review"]["models"] == []
    assert provenance["inference_location"] == "local"
    assert provenance["roles"]["decision"]["inference_location"] == "local"
    assert provenance["roles"]["review"]["inference_location"] == "mock"
    assert provenance["roles"]["review"]["selections"] == [
        {"backend": "mock", "configured_model": None}
    ]

    artifact["providers"]["review"] = {
        "backend": "ollama",
        "endpoint": "https://models.example.com",
        "model": "reviewer-v1",
    }
    artifact["review"] = {"models": {}}
    (tmp_path / "case-a.experiment.json").write_text(json.dumps(artifact))
    decider_local = collect(tmp_path, ["case-a"])
    assert decider_local["inference_location"] == "mixed"
    assert decider_local["roles"]["decision"]["inference_location"] == "local"
    assert decider_local["roles"]["review"]["inference_location"] == "hosted_api"

    artifact["providers"]["decision"]["endpoint"] = "https://models.example.com"
    artifact["providers"]["review"]["endpoint"] = "http://localhost:11434"
    (tmp_path / "case-a.experiment.json").write_text(json.dumps(artifact))
    reviewer_local = collect(tmp_path, ["case-a"])
    assert reviewer_local["inference_location"] == "mixed"
    assert reviewer_local["roles"]["decision"]["inference_location"] == "hosted_api"
    assert reviewer_local["roles"]["review"]["inference_location"] == "local"


def test_rescoring_requires_the_original_input_binding(tmp_path, evaluation_summary):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    namespace = runpy.run_path(str(ROOT / "benchmark.py"))
    namespace["input_binding"].__globals__["ROOT"] = root
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(evaluation_summary))
    binding = namespace["input_binding"](
        "evaluation", root / "policy.toml", root / "providers.toml"
    )
    with pytest.raises(ValueError, match="Missing.*rerun"):
        namespace["verify_binding"](summary_path, binding)
    namespace["write_binding"](summary_path, binding)
    namespace["verify_binding"](summary_path, binding)
    path = root / "prs" / "pr-126" / "evidence.json"
    evidence = json.loads(path.read_text())
    evidence["body"] += "\nAdditional supplied prose.\n"
    path.write_text(json.dumps(evidence))
    # Prediction binding still rejects changed supplied evidence, independently of label revision.
    changed = copy.deepcopy(binding)
    changed["case_inputs_sha256"]["pr-126"] = namespace["case_inputs_sha256"](
        {
            "evidence": "prs/pr-126/evidence.json",
            "metadata": "prs/pr-126/metadata.json",
            "review": "prs/pr-126/review.md",
        }
    )
    with pytest.raises(ValueError, match="Stale.*rerun"):
        namespace["verify_binding"](summary_path, changed)


def test_summary_cannot_overclaim_human_review(evaluation_summary):
    evaluation_summary["label_status"] = "human_reviewed"
    with pytest.raises(ValueError, match="provenance"):
        _score(evaluation_summary)


def test_authored_reviews_preserve_uncertain_adjudications_and_original_testing_label():
    archive = json.loads((ROOT / "independent-review" / "authored-reviews.json").read_text())
    uncertain = {
        (case_id, key)
        for case_id, record in archive["cases"].items()
        for key, label in record["labels"].items()
        if label["adjudicated"] == "uncertain" and key == "change-triviality"
    }
    assert uncertain == {
        (f"pr-{number}", "change-triviality") for number in (128, 129, 130, 131, 144, 145)
    }
    corrected = archive["cases"]["pr-137"]["labels"]["testing-explanation"]
    assert corrected["initial_author_label"] == "passed"
    assert corrected["adjudicated"] == corrected["oracle"] == "failed"


def test_validator_rejects_stale_authored_review_inputs(tmp_path):
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    path = root / "prs" / "pr-126" / "evidence.json"
    evidence = json.loads(path.read_text())
    evidence["body"] += "\nNew supplied details.\n"
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="re-review"):
        runpy.run_path(str(ROOT / "validate.py"))["validate"](root)
