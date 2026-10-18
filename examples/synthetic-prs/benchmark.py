"""Run the router's rubric evaluation on the synthetic corpus and score versioned reference judgments."""

import argparse
import hashlib
import ipaddress
import json
import os
import random
import runpy
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from pr_review_router.corpus_metrics import rubric_metrics, stratify
from pr_review_router.corpus_references import reference_revisions

ROOT = Path(__file__).resolve().parent
RUBRIC_TRIVIALITY = "change-triviality"


def sample_cases(size: int, seed: int) -> list[str]:
    """Sample in balanced family rounds without replacement.

    :param size: Number of cases requested from the full corpus.
    :param seed: Recorded seed used to reproduce selection.
    :returns: Selected case identifiers in canonical corpus order.
    :raises ValueError: If the requested size is outside the corpus range.
    """
    cases = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))["cases"]
    if not 1 <= size <= len(cases):
        raise ValueError(f"Sample size must be between 1 and {len(cases)}")
    families = {}
    for case in cases:
        families.setdefault(case["family_id"], []).append(case["case_id"])
    generator = random.Random(seed)
    selected = set()
    while len(selected) < size:
        family_ids = sorted(family for family, members in families.items() if members)
        generator.shuffle(family_ids)
        for family in family_ids:
            members = families[family]
            selected.add(members.pop(generator.randrange(len(members))))
            if len(selected) == size:
                break
    return [case["case_id"] for case in cases if case["case_id"] in selected]


def run_router(
    output_dir: Path,
    *,
    config: Path,
    providers: Path,
    replay_dir: Path | None,
    record: bool,
    split: str = "evaluation",
    reference_revision: str = "latest",
    selected_case_ids: list[str] | None = None,
) -> dict:
    """Run ``pr-review-router evaluate`` without environment policy overrides.

    :param output_dir: Directory for per-case router outputs and ``summary.json``.
    :param config: Routing policy TOML.
    :param providers: Provider configuration TOML.
    :param replay_dir: Optional directory of recorded tapes for offline replay.
    :param record: Whether to record HTTP exchanges for later replay.
    :param split: Family-disjoint corpus partition, or all cases explicitly.
    :param reference_revision: Grading revision, validated before any provider call.
    :param selected_case_ids: Exact random sample, required for the random split.
    :returns: The router's evaluation summary.
    :raises subprocess.CalledProcessError: If the router rejects inputs or replay.
    """
    grading_references(split, reference_revision, selected_case_ids)
    binding = input_binding(split, config, providers, selected_case_ids)
    corpus_path = ROOT / ("corpus.json" if split == "all" else f"corpus-{split}.json")
    if split == "random":
        corpus = json.loads((ROOT / "corpus.json").read_text(encoding="utf-8"))
        corpus["cases"] = [
            case for case in corpus["cases"] if case["case_id"] in binding["selected_case_ids"]
        ]
        sample_root = output_dir / "sample-inputs"
        sources = {corpus["assessment_rubric"]}
        for case in corpus["cases"]:
            for key in ("evidence", "review"):
                if case.get(key):
                    sources.add(case[key])
        for relative in sources:
            source = (ROOT / relative).resolve()
            if Path(relative).is_absolute() or not source.is_relative_to(ROOT.resolve()):
                raise ValueError("Sample inputs must stay beneath the corpus directory")
            destination = sample_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        corpus_path = (sample_root / "corpus.json").resolve()
        corpus_path.write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    command = [
        sys.executable,
        "-c",
        "import sys; from pr_review_router.cli import main; sys.exit(main())",
        "evaluate",
        "--corpus",
        str(corpus_path),
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
    if input_binding(split, config, providers, selected_case_ids) != binding:
        raise ValueError("Benchmark inputs changed during evaluation; rerun with frozen inputs")
    write_binding(output_dir / "summary.json", binding)
    return json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))


def grading_references(
    split: str, revision: str = "latest", selected_case_ids: list[str] | None = None
):
    """Load a complete grading revision and validate each selected archive.

    :param split: Required case partition.
    :param revision: Original baseline, latest alias, or registered review snapshot.
    :param selected_case_ids: Exact random sample when using the random split.
    :returns: Policy-derived references with archive provenance.
    """
    validation = runpy.run_path(str(ROOT / "validate.py"))
    result = validation["load_grading_references"](
        ROOT, "all" if split == "random" else split, revision
    )
    if split == "random":
        selected = select_cases(split, selected_case_ids)
        cases = {case["case_id"]: result.cases[case["case_id"]] for case in selected}
        result = replace(
            result, cases=cases, metadata={**result.metadata, "case_count": len(cases)}
        )
    return result


def select_cases(split: str, selected_case_ids: list[str] | None = None) -> list[dict]:
    """Validate a named partition or an explicitly pinned random sample.

    :param split: Named partition, all cases, or random selection.
    :param selected_case_ids: Unique known case identifiers for a random sample.
    :returns: Selected index entries in canonical order.
    :raises ValueError: If the partition or sample is invalid.
    """
    cases = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))["cases"]
    if split == "random":
        known = {case["case_id"] for case in cases}
        if (
            not selected_case_ids
            or len(selected_case_ids) != len(set(selected_case_ids))
            or not set(selected_case_ids) <= known
        ):
            raise ValueError("Random sample requires unique known selected case IDs")
        return [case for case in cases if case["case_id"] in selected_case_ids]
    if split not in {"all", "development", "evaluation"}:
        raise ValueError("Unknown corpus split")
    return [case for case in cases if split == "all" or case["split"] == split]


def score(
    summary: dict,
    split: str = "all",
    reference_revision: str = "latest",
    *,
    prediction_references: dict | None = None,
    selected_case_ids: list[str] | None = None,
) -> dict:
    """Compare router rubric outputs with the selected revision of reference judgments.

    :param summary: Router evaluation summary for the synthetic corpus.
    :param split: Required partition; every selected case must occur exactly once.
    :param reference_revision: Selected grading labels; latest adjudications by default.
    :param prediction_references: Bound original labels from the prediction run, when rescoring.
    :param selected_case_ids: Exact random sample used for predictions.
    :returns: Overall and stratified confusion, recommendation risks, and triviality metrics.
    :raises ValueError: If case coverage or embedded references differ from pinned inputs.
    """
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    validation = runpy.run_path(str(ROOT / "validate.py"))
    validation["validate_splits"](ROOT, index)
    if summary.get("label_status", index["label_status"]) != index["label_status"]:
        raise ValueError("Summary label provenance differs from the provisional corpus")
    levels = {level["label"]: level["score"] for level in index["triviality_scale"]["levels"]}
    selected = select_cases(split, selected_case_ids)
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
    revision = grading_references(split, reference_revision, selected_case_ids)
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


def input_binding(
    split: str, config: Path, providers: Path, selected_case_ids: list[str] | None = None
) -> dict:
    """Capture the selected inputs before inference and validate fixed family assignments.

    :param split: Required corpus partition.
    :param config: Policy actually supplied to the evaluation.
    :param providers: Provider configuration actually supplied to the evaluation.
    :param selected_case_ids: Exact random sample used for predictions.
    :returns: Content fingerprints bound to the selected case IDs.
    """
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    validation = runpy.run_path(str(ROOT / "validate.py"))
    validation["validate_splits"](ROOT, index)
    selected = select_cases(split, selected_case_ids)
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


def collect_model_provenance(output_dir: Path, case_ids: list[str]) -> dict:
    """Collect configured and observed model identities from case artifacts.

    :param output_dir: Directory containing per-case experiment artifacts.
    :param case_ids: Selected benchmark cases in prediction order.
    :returns: Model selections, observed identities, and artifact coverage.
    :raises ValueError: If an available experiment artifact is invalid.
    """
    missing = []
    configurations = {role: set() for role in ("decision", "review")}
    models = {role: set() for role in ("decision", "review")}
    locations = {role: set() for role in ("decision", "review")}
    found = 0
    for case_id in case_ids:
        path = output_dir / f"{case_id}.experiment.json"
        if not path.is_file():
            missing.append(case_id)
            continue
        artifact = json.loads(path.read_text(encoding="utf-8"))
        found += 1
        for role in configurations:
            provider = artifact.get("providers", {}).get(role, {})
            if not isinstance(provider, dict):
                raise ValueError(f"Invalid {role} provider provenance in {path}")
            configurations[role].add((provider.get("backend"), provider.get("model")))
            if provider.get("backend") == "mock":
                locations[role].add("mock")
            else:
                endpoint = provider.get("endpoint")
                if isinstance(endpoint, str):
                    hostname = urlsplit(endpoint).hostname
                    try:
                        is_loopback = (
                            hostname == "localhost"
                            or ipaddress.ip_address(hostname or "").is_loopback
                        )
                    except ValueError:
                        is_loopback = False
                    locations[role].add("local" if is_loopback else "hosted_api")
            diagnostics = artifact.get(role) or {}
            if not isinstance(diagnostics, dict):
                raise ValueError(f"Invalid {role} model diagnostics in {path}")
            observed = diagnostics.get("models", {})
            if not isinstance(observed, dict):
                raise ValueError(f"Invalid {role} model identities in {path}")
            for name, metadata in observed.items():
                if not isinstance(metadata, dict):
                    raise ValueError(f"Invalid {role} model identity in {path}")
                models[role].add((name, metadata.get("digest"), metadata.get("server_version")))

    def location(values: set[str]) -> str:
        return "unavailable" if not values else next(iter(values)) if len(values) == 1 else "mixed"

    inference_locations = {role: location(values) for role, values in locations.items()}
    model_locations = set().union(*locations.values()) - {"mock"}
    inference_location = (
        location(model_locations)
        if model_locations
        else "mock"
        if any("mock" in values for values in locations.values())
        else "unavailable"
    )
    return {
        "status": "unavailable" if found == 0 else "complete" if not missing else "partial",
        "artifacts_found": found,
        "artifacts_expected": len(case_ids),
        "missing_case_ids": missing,
        "inference_location": inference_location,
        "roles": {
            role: {
                "inference_location": inference_locations[role],
                "selections": [
                    {"backend": backend, "configured_model": model}
                    for backend, model in sorted(
                        configurations[role], key=lambda item: (item[0] or "", item[1] or "")
                    )
                ],
                "models": [
                    {"model": name, "digest": digest, "server_version": server_version}
                    for name, digest, server_version in sorted(
                        models[role], key=lambda item: (item[0], item[1] or "", item[2] or "")
                    )
                ],
            }
            for role in configurations
        },
    }


def main() -> None:
    """Run or replay the benchmark and save ``benchmark.json`` beside the router outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/synthetic-prs-benchmark"))
    parser.add_argument("--config", type=Path, default=ROOT / "policy.toml")
    parser.add_argument("--providers-config", type=Path, default=ROOT / "providers.toml")
    parser.add_argument(
        "--split", choices=["random", "evaluation", "development", "all"], default=None
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Random sample size (default: 60; quick run: 30).",
    )
    parser.add_argument(
        "--seed", type=int, help="Reproduce a random sample; otherwise generate a seed."
    )
    parser.add_argument(
        "--case-id",
        dest="case_ids",
        action="append",
        help="Explicit case to evaluate; repeat for multiple cases, instead of sampling.",
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
    selected_case_ids = None
    sampling = None
    try:
        if args.summary:
            if args.case_ids:
                raise ValueError("--case-id cannot be combined with --summary")
            saved = json.loads(
                args.summary.with_name("benchmark-inputs.json").read_text(encoding="utf-8")
            )
            args.split = args.split or saved["split"]
            if args.split == "random":
                selected_case_ids = saved["selected_case_ids"]
                sampling = saved.get("sampling")
                if args.seed is not None and args.seed != (sampling or {}).get("seed"):
                    raise ValueError("Seed differs from the original prediction sample")
                if args.sample_size is not None and args.sample_size != len(selected_case_ids):
                    raise ValueError("Sample size differs from the original prediction sample")
        else:
            args.split = args.split or "random"
            if args.case_ids:
                if args.split != "random":
                    raise ValueError("--case-id requires the default random split")
                if args.seed is not None or args.sample_size is not None:
                    raise ValueError("--case-id cannot be combined with --seed or --sample-size")
                select_cases("random", args.case_ids)
                selected_case_ids = args.case_ids
                sampling = {
                    "method": "explicit_case_ids",
                    "seed": None,
                    "size": len(selected_case_ids),
                }
            elif args.split == "random":
                seed = args.seed if args.seed is not None else random.SystemRandom().getrandbits(64)
                size = args.sample_size if args.sample_size is not None else 60
                selected_case_ids = sample_cases(size, seed)
                sampling = {"method": "family_balanced_random", "seed": seed, "size": size}
        if args.split != "random" and (args.seed is not None or args.sample_size is not None):
            raise ValueError("--seed and --sample-size require --split random")
        grading_references(args.split, args.reference_revision, selected_case_ids)
        if args.summary:
            prediction_references = verify_binding(
                args.summary,
                input_binding(args.split, args.config, args.providers_config, selected_case_ids),
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
            selected_case_ids=selected_case_ids,
        )
    )
    binding = input_binding(args.split, args.config, args.providers_config, selected_case_ids)
    if sampling is not None:
        binding["sampling"] = sampling
        if not args.summary:
            write_binding(args.output_dir / "summary.json", binding)
    try:
        metrics = score(
            summary,
            args.split,
            args.reference_revision,
            prediction_references=prediction_references,
            selected_case_ids=selected_case_ids,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    benchmark = {
        "benchmark_version": 4,
        "mode": summary["mode"],
        "sampling": sampling,
        "model_provenance": collect_model_provenance(
            args.summary.parent if args.summary else args.output_dir,
            binding["selected_case_ids"],
        ),
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
