"""Run the router's rubric evaluation on the synthetic corpus and score versioned reference judgments."""

import argparse
import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

from pr_review_router.corpus_metrics import rubric_metrics, stratify
from pr_review_router.corpus_references import reference_revisions

ROOT = Path(__file__).resolve().parent
RUBRIC_TRIVIALITY = "change-triviality"


def run_router(
    output_dir: Path,
    *,
    config: Path,
    providers: Path,
    replay_dir: Path | None,
    record: bool,
    split: str = "evaluation",
    reference_revision: str = "latest",
) -> dict:
    """Run ``pr-review-router evaluate`` without environment policy overrides.

    :param output_dir: Directory for per-case router outputs and ``summary.json``.
    :param config: Routing policy TOML.
    :param providers: Provider configuration TOML.
    :param replay_dir: Optional directory of recorded tapes for offline replay.
    :param record: Whether to record HTTP exchanges for later replay.
    :param split: Family-disjoint corpus partition, or all cases explicitly.
    :param reference_revision: Grading revision, validated before any provider call.
    :returns: The router's evaluation summary.
    :raises subprocess.CalledProcessError: If the router rejects inputs or replay.
    """
    grading_references(split, reference_revision)
    binding = input_binding(split, config, providers)
    command = [
        sys.executable,
        "-c",
        "import sys; from pr_review_router.cli import main; sys.exit(main())",
        "evaluate",
        "--corpus",
        str(ROOT / ("corpus.json" if split == "all" else f"corpus-{split}.json")),
        "--config",
        str(config),
        "--providers-config",
        str(providers),
        "--output-dir",
        str(output_dir),
    ]
    if replay_dir:
        command += ["--replay-dir", str(replay_dir)]
    if record:
        command.append("--record")
    # Environment overrides would silently change the pinned policy.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PR_REVIEW_ROUTER_")
    }
    subprocess.run(command, check=True, env=environment, stdout=subprocess.DEVNULL)
    if input_binding(split, config, providers) != binding:
        raise ValueError("Benchmark inputs changed during evaluation; rerun with frozen inputs")
    write_binding(output_dir / "summary.json", binding)
    return json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))


def grading_references(split: str, revision: str = "latest"):
    """Load a complete grading revision and validate each selected archive.

    :param split: Required case partition.
    :param revision: Original baseline, latest alias, or registered review snapshot.
    :returns: Policy-derived references with archive provenance.
    """
    validation = runpy.run_path(str(ROOT / "validate.py"))
    return validation["load_grading_references"](ROOT, split, revision)


def score(
    summary: dict,
    split: str = "all",
    reference_revision: str = "latest",
    *,
    prediction_references: dict | None = None,
) -> dict:
    """Compare router rubric outputs with the selected revision of reference judgments.

    :param summary: Router evaluation summary for the synthetic corpus.
    :param split: Required partition; every selected case must occur exactly once.
    :param reference_revision: Selected grading labels; latest adjudications by default.
    :param prediction_references: Bound original labels from the prediction run, when rescoring.
    :returns: Overall and stratified confusion, recommendation risks, and triviality metrics.
    :raises ValueError: If case coverage or embedded references differ from pinned inputs.
    """
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    validation = runpy.run_path(str(ROOT / "validate.py"))
    validation["validate_splits"](ROOT, index)
    if summary.get("label_status", index["label_status"]) != index["label_status"]:
        raise ValueError("Summary label provenance differs from the provisional corpus")
    levels = {level["label"]: level["score"] for level in index["triviality_scale"]["levels"]}
    if split not in {"all", "development", "evaluation"}:
        raise ValueError("Unknown corpus split")
    selected = [case for case in index["cases"] if split == "all" or case["split"] == split]
    corpus = json.loads((ROOT / "corpus.json").read_text(encoding="utf-8"))
    origins = (
        prediction_references
        if prediction_references is not None
        else {
            case["case_id"]: case["expectations"]["rubric"]
            for case in corpus["cases"]
            if case["case_id"] in {item["case_id"] for item in selected}
        }
    )
    ids = [case["case_id"] for case in summary["cases"]]
    if len(ids) != len(set(ids)) or set(ids) != {case["case_id"] for case in selected}:
        raise ValueError("Results must cover exactly the selected case IDs once")
    embedded = summary["reference_labels"]["cases"]
    embedded_ids = [case["case_id"] for case in embedded]
    if (
        len(embedded_ids) != len(set(embedded_ids))
        or {case["case_id"]: case["expectations"]["rubric"] for case in embedded} != origins
    ):
        raise ValueError("Summary reference labels differ from the pinned selected corpus")
    outputs = {case["case_id"]: case.get("rubric") or {} for case in summary["cases"]}
    rows = []
    revision = grading_references(split, reference_revision)
    for case in selected:
        actual = outputs[case["case_id"]]
        answer = (actual.get("native_answers") or {}).get(RUBRIC_TRIVIALITY) or {}
        label = answer.get("choice") or "unanswered"
        if label not in {*levels, "uncertain", "unanswered"}:
            raise ValueError(f"Invalid native triviality label: {label}")
        reference = revision.cases[case["case_id"]]
        contested = reference["contested_criteria"]
        rows.append(
            {
                **case,
                "author_triviality_score": case["triviality_score"],
                "author_triviality_level": case["triviality_level"],
                "triviality_level": reference["triviality_level"],
                "reference_score": reference["triviality_score"],
                "reference_label": reference["triviality_level"],
                "predicted_score": levels.get(label),
                "predicted_label": label,
                "expected": reference,
                "actual": actual,
                "contested_criteria": contested,
                "reference_confidence": "author_intent"
                if revision.metadata["kind"] == "author_intent"
                else "contested"
                if contested
                else "uncontested_ai",
            }
        )
    groups = stratify(rows, rubric_metrics)
    return {
        "split": split,
        "selected_case_ids": [case["case_id"] for case in selected],
        "grading_reference": revision.metadata,
        "grading_references": revision.cases,
        **rubric_metrics(rows),
        **groups,
        "macro_scenario_recommendation_agreement": sum(
            group["recommendation"]["agreement"] for group in groups["by_scenario"].values()
        )
        / len(groups["by_scenario"]),
    }


def case_inputs_sha256(case: dict) -> str:
    """Fingerprint supplied evidence, commits, and optional review contents.

    :param case: Index entry naming the supplied inputs.
    :returns: SHA-256 digest with file-role separators.
    """
    digest = hashlib.sha256()
    for role in ("evidence", "metadata", "review"):
        digest.update(role.encode() + b"\0")
        if case.get(role):
            digest.update((ROOT / case[role]).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def input_binding(split: str, config: Path, providers: Path) -> dict:
    """Capture the selected inputs before inference and validate fixed family assignments.

    :param split: Required corpus partition.
    :param config: Policy actually supplied to the evaluation.
    :param providers: Provider configuration actually supplied to the evaluation.
    :returns: Content fingerprints bound to the selected case IDs.
    """
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    validation = runpy.run_path(str(ROOT / "validate.py"))
    validation["validate_splits"](ROOT, index)
    selected = [case for case in index["cases"] if split == "all" or case["split"] == split]
    paths = {name: ROOT / name for name in ("splits.json", "rubric.toml")}
    paths.update({"policy.toml": config, "providers.toml": providers})
    return {
        "schema_version": 2,
        "split": split,
        "selected_case_ids": [case["case_id"] for case in selected],
        "inputs_sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()
        },
        "case_inputs_sha256": {case["case_id"]: case_inputs_sha256(case) for case in selected},
        "prediction_reference_labels": {
            case["case_id"]: case["expectations"]["rubric"]
            for case in json.loads((ROOT / "corpus.json").read_text())["cases"]
            if case["case_id"] in {item["case_id"] for item in selected}
        },
    }


def write_binding(summary_path: Path, binding: dict) -> None:
    """Bind a completed summary to the input snapshot captured before evaluation.

    :param summary_path: Completed summary file, never replaced by this function.
    :param binding: Pre-execution input fingerprints.
    """
    sidecar = {**binding, "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest()}
    summary_path.with_name("benchmark-inputs.json").write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
    )


def verify_binding(summary_path: Path, expected: dict) -> dict:
    """Reject unbound or stale summaries before attributing results to current inputs.

    :param summary_path: Summary to rescore offline.
    :param expected: Fingerprints of the currently selected inputs.
    :returns: Verified labels used by the original prediction run, independently of grading revision.
    :raises ValueError: If the sidecar is missing or input/output content changed.
    """
    sidecar = summary_path.with_name("benchmark-inputs.json")
    if not sidecar.is_file():
        raise ValueError("Missing benchmark input binding; rerun the benchmark to create it")
    actual = json.loads(sidecar.read_text(encoding="utf-8"))
    if (
        actual.get("schema_version") not in {1, 2}
        or any(
            actual.get(key) != expected[key]
            for key in ("split", "selected_case_ids", "case_inputs_sha256")
        )
        or any(
            actual.get("inputs_sha256", {}).get(key) != value
            for key, value in expected["inputs_sha256"].items()
        )
        or actual.get("summary_sha256") != hashlib.sha256(summary_path.read_bytes()).hexdigest()
    ):
        raise ValueError("Stale benchmark inputs or summary; rerun with the selected frozen inputs")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if actual["schema_version"] == 1:
        if (
            actual["inputs_sha256"].get("corpus.json")
            != hashlib.sha256((ROOT / "corpus.json").read_bytes()).hexdigest()
        ):
            raise ValueError(
                "Legacy prediction labels cannot be verified; rerun to create a version-2 binding"
            )
        origins = expected["prediction_reference_labels"]
    else:
        origins = actual.get("prediction_reference_labels")
    embedded = summary["reference_labels"]["cases"]
    if (
        not isinstance(origins, dict)
        or set(origins) != set(expected["selected_case_ids"])
        or len(embedded) != len(origins)
        or {case["case_id"]: case["expectations"]["rubric"] for case in embedded} != origins
    ):
        raise ValueError("Summary prediction-origin references differ from their pinned binding")
    return origins


def main() -> None:
    """Run or replay the benchmark and save ``benchmark.json`` beside the router outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/synthetic-prs-benchmark"))
    parser.add_argument("--config", type=Path, default=ROOT / "policy.toml")
    parser.add_argument("--providers-config", type=Path, default=ROOT / "providers.toml")
    parser.add_argument(
        "--split", choices=["evaluation", "development", "all"], default="evaluation"
    )
    parser.add_argument(
        "--reference-revision",
        default="latest",
        help="Grade latest adjudications (default), original author labels, or a named review revision.",
    )
    parser.add_argument(
        "--list-reference-revisions",
        action="store_true",
        help="List registered grading revisions without inference.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay-dir", type=Path, help="Replay recorded tapes without inference.")
    mode.add_argument("--record", action="store_true", help="Record tapes for later replay.")
    mode.add_argument("--summary", type=Path, help="Score an existing evaluation summary offline.")
    args = parser.parse_args()
    if args.list_reference_revisions:
        print(json.dumps(reference_revisions(ROOT), indent=2))
        return
    prediction_references = None
    try:
        grading_references(args.split, args.reference_revision)
        if args.summary:
            prediction_references = verify_binding(
                args.summary, input_binding(args.split, args.config, args.providers_config)
            )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    summary = (
        json.loads(args.summary.read_text(encoding="utf-8"))
        if args.summary
        else run_router(
            args.output_dir,
            config=args.config,
            providers=args.providers_config,
            replay_dir=args.replay_dir,
            record=args.record,
            split=args.split,
            reference_revision=args.reference_revision,
        )
    )
    binding = input_binding(args.split, args.config, args.providers_config)
    try:
        metrics = score(
            summary,
            args.split,
            args.reference_revision,
            prediction_references=prediction_references,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    benchmark = {
        "benchmark_version": 3,
        "mode": summary["mode"],
        "label_status": json.loads((ROOT / "index.json").read_text())["label_status"],
        "prediction_inputs_sha256": binding["inputs_sha256"],
        "prediction_summary_sha256": hashlib.sha256(
            (args.summary or args.output_dir / "summary.json").read_bytes()
        ).hexdigest(),
        "failures": {
            key: summary["totals"][key]
            for key in ("schema_failures", "transport_failures", "rubric_schema_failures")
        },
        "case_inputs_sha256": binding["case_inputs_sha256"],
        **metrics,
        "limitations": (
            "Selected reference judgments remain provisional, not human-reviewed. "
            "Uncertain references are retained explicitly; numeric ordinal accuracy uses only known reference levels. "
            "Raw summary labels retain prediction-run provenance; benchmark.json grades the selected reference revision. "
            "Some cases supply no review or an unresolvable one, so their "
            "review coverage is unassessable. Replay validates recorded behavior, not "
            "current model quality."
        ),
    }
    path = args.output_dir / "benchmark.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(benchmark, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: benchmark[key] for key in ("recommendation", "failures")}, indent=2))


if __name__ == "__main__":
    main()
