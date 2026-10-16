"""Run the router's rubric evaluation on the synthetic corpus and score it against the oracle."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUBRIC_TRIVIALITY = "change-triviality"


def run_router(
    output_dir: Path, *, config: Path, providers: Path, replay_dir: Path | None, record: bool
) -> dict:
    """Run ``pr-review-router evaluate`` without environment policy overrides.

    :param output_dir: Directory for per-case router outputs and ``summary.json``.
    :param config: Routing policy TOML.
    :param providers: Provider configuration TOML.
    :param replay_dir: Optional directory of recorded tapes for offline replay.
    :param record: Whether to record HTTP exchanges for later replay.
    :returns: The router's evaluation summary.
    :raises subprocess.CalledProcessError: If the router rejects inputs or replay.
    """
    command = [
        sys.executable,
        "-c",
        "import sys; from pr_review_router.cli import main; sys.exit(main())",
        "evaluate",
        "--corpus",
        str(ROOT / "corpus.json"),
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
    return json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))


def score(summary: dict) -> dict:
    """Compare router rubric outputs with oracle-derived labels and triviality scores.

    :param summary: Router evaluation summary for the synthetic corpus.
    :returns: Per-criterion agreement, recommendation agreement, and triviality metrics.
    """
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    levels = {level["label"]: level["score"] for level in index["triviality_scale"]["levels"]}
    references = {
        case["case_id"]: case["expectations"]["rubric"]
        for case in summary["reference_labels"]["cases"]
    }
    oracle_scores = {case["case_id"]: case["triviality_score"] for case in index["cases"]}
    criteria: dict[str, Counter] = {}
    recommendations = Counter()
    triviality = Counter()
    predicted_scores = {}
    for case in summary["cases"]:
        expected = references[case["case_id"]]
        actual = case["rubric"]
        for criterion_id, status in expected["criteria"].items():
            criteria.setdefault(criterion_id, Counter())[
                f"{status}->{actual['criteria'].get(criterion_id)}"
            ] += 1
        recommendations[f"{expected['recommendation']}->{actual['recommendation']}"] += 1
        answer = actual["native_answers"].get(RUBRIC_TRIVIALITY) or {}
        predicted = levels.get(answer.get("choice", ""))
        predicted_scores[case["case_id"]] = predicted
        triviality[f"{oracle_scores[case['case_id']]}->{answer.get('choice', 'unanswered')}"] += 1
    classified = {key: value for key, value in predicted_scores.items() if value is not None}
    errors = [abs(value - oracle_scores[key]) for key, value in classified.items()]
    count = len(summary["cases"])
    return {
        "case_count": count,
        "criteria": {
            criterion_id: {
                "agreement": sum(n for pair, n in pairs.items() if len(set(pair.split("->"))) == 1)
                / count,
                "expected_to_actual": dict(sorted(pairs.items())),
            }
            for criterion_id, pairs in criteria.items()
        },
        "recommendation": {
            "agreement": sum(
                n for pair, n in recommendations.items() if len(set(pair.split("->"))) == 1
            )
            / count,
            "expected_to_actual": dict(sorted(recommendations.items())),
            "unsafe_approvals": summary["totals"]["unsafe_rubric_approvals"],
        },
        "triviality": {
            "classified": len(classified),
            "exact_accuracy": errors.count(0) / count,
            "exact_accuracy_when_classified": errors.count(0) / len(errors) if errors else None,
            "mean_absolute_error_when_classified": sum(errors) / len(errors) if errors else None,
            "binary_accuracy": sum(
                (value <= 1) == (oracle_scores[key] <= 1) for key, value in classified.items()
            )
            / count,
            "reference_score_to_label": dict(sorted(triviality.items())),
            "predicted_scores": predicted_scores,
        },
    }


def reviews_sha256() -> str:
    """Hash every supplied review text in case order, since the corpus file only names them.

    :returns: Hexadecimal digest of the case-ordered review paths and contents.
    """
    digest = hashlib.sha256()
    for case in json.loads((ROOT / "corpus.json").read_text(encoding="utf-8"))["cases"]:
        if case.get("review"):
            digest.update(case["review"].encode() + b"\0")
            digest.update((ROOT / case["review"]).read_bytes() + b"\0")
    return digest.hexdigest()


def main() -> None:
    """Run or replay the benchmark and save ``benchmark.json`` beside the router outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/synthetic-prs-benchmark"))
    parser.add_argument("--config", type=Path, default=ROOT / "policy.toml")
    parser.add_argument("--providers-config", type=Path, default=ROOT / "providers.toml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay-dir", type=Path, help="Replay recorded tapes without inference.")
    mode.add_argument("--record", action="store_true", help="Record tapes for later replay.")
    args = parser.parse_args()
    summary = run_router(
        args.output_dir,
        config=args.config,
        providers=args.providers_config,
        replay_dir=args.replay_dir,
        record=args.record,
    )
    inputs = {
        "corpus.json": ROOT / "corpus.json",
        "rubric.toml": ROOT / "rubric.toml",
        "index.json": ROOT / "index.json",
        "policy.toml": args.config,
        "providers.toml": args.providers_config,
    }
    benchmark = {
        "benchmark_version": 1,
        "mode": summary["mode"],
        "label_status": summary["label_status"],
        "inputs_sha256": {
            **{
                name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in inputs.items()
            },
            "review texts": reviews_sha256(),
        },
        "failures": {
            key: summary["totals"][key]
            for key in ("schema_failures", "transport_failures", "rubric_schema_failures")
        },
        **score(summary),
        "limitations": (
            "Oracle labels are provisional synthetic-author intent, not human-reviewed. "
            "Some cases supply no review or an unresolvable one, so their "
            "review coverage is unassessable. Replay validates recorded behavior, not "
            "current model quality."
        ),
    }
    path = args.output_dir / "benchmark.json"
    path.write_text(json.dumps(benchmark, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: benchmark[key] for key in ("recommendation", "failures")}, indent=2))


if __name__ == "__main__":
    main()
