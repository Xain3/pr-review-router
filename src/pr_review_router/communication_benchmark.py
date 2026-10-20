"""A separately versioned benchmark using preserved communication annotations."""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .assessment_engine import assess_pull_request
from .assessment_rubric import (
    AssessmentRubric,
    CriterionAssessment,
    CriterionBatch,
    load_assessment_rubric,
)
from .assessment_runtime import DecisionConfig, run_assessment
from .config import Policy
from .contracts import PullRequestEvidence
from .model_catalog import load_decider_settings, resolve_catalog_path
from .transport import fingerprint


class _References:
    """Offline gate-scoring input, never supplied to a model adapter."""

    def __init__(self, batch: CriterionBatch) -> None:
        """Bind the separate expected criterion statuses.

        :param batch: Reference labels selected after model execution.
        """
        self.batch = batch

    def assess(self, evidence: PullRequestEvidence) -> CriterionBatch:
        """Return annotations only to the offline reference scorer.

        :param evidence: Evidence already evaluated independently by the model.
        :returns: The separately loaded expected statuses.
        """
        return self.batch


def _source(root: Path, relative: str) -> Path:
    """Confine manifest paths, including symbolic links, to the corpus.

    :param root: Resolved corpus directory.
    :param relative: Manifest-linked file.
    :returns: Confined source path.
    :raises ValueError: If the path escapes the corpus.
    """
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError("benchmark source leaves the corpus directory")
    return path


def _decision_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count verdict agreement and unresolved results for a supported stratum.

    :param rows: Expected and actual decisions for one stratum.
    :returns: Support, agreement, confusion, false acceptance, and unresolved counts.
    """
    return {
        "support": len(rows),
        "decision_agreement": sum(r["expected"] == r["actual"] for r in rows) / len(rows),
        "confusion": dict(Counter(f"{r['expected']}->{r['actual']}" for r in rows)),
        "false_acceptance": sum(
            r["actual"] == "accepted" and r["expected"] != "accepted" for r in rows
        ),
        "unresolved": sum(r["actual"] == "unresolved" for r in rows),
    }


def run_benchmark(
    root: Path,
    rubric: AssessmentRubric,
    settings: DecisionConfig,
    output_dir: Path,
    *,
    split: str = "evaluation",
    replay_dir: Path | None = None,
    record: bool = False,
) -> dict[str, Any]:
    """Assess evidence independently, then compare with provisional author annotations.

    :param root: Preserved synthetic corpus directory.
    :param rubric: Communication-only rubric using canonical annotation identifiers.
    :param settings: Explicit decision model selection or offline mock.
    :param output_dir: Runtime output outside the corpus.
    :param split: Frozen development/evaluation partition, or all cases.
    :param replay_dir: Exact assessment-only tapes, without live fallback.
    :param record: Explicitly record sanitized model exchanges.
    :returns: Versioned criterion and verdict metrics with support counts.
    :raises ValueError: If corpus paths, labels, selection, or output locations are invalid.
    """
    from .cli import _write_report

    root = root.resolve()
    if output_dir.resolve().is_relative_to(root):
        raise ValueError("benchmark outputs must be outside the preserved corpus")
    if replay_dir and record:
        raise ValueError("recording and replay are mutually exclusive")
    if (
        rubric.enforcement_rules
        or rubric.routing_rules
        or any(c.target == "review" for c in rubric.criteria)
    ):
        raise ValueError("this benchmark requires communication criteria with explicit actions")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    splits = json.loads((root / "splits.json").read_text(encoding="utf-8"))
    if split not in {"development", "evaluation", "all"}:
        raise ValueError("unknown benchmark partition")
    selected = None if split == "all" else set(splits["partitions"][split]["case_ids"])
    cases = [c for c in index["cases"] if selected is None or c["case_id"] in selected]
    ids = [c["case_id"] for c in cases]
    if not cases or len(set(ids)) != len(ids) or (selected is not None and set(ids) != selected):
        raise ValueError("benchmark selection is incomplete or duplicated")
    # Validate every label and path before any inference or report writes.
    prepared = []
    for case in cases:
        if re.fullmatch(r"[a-z0-9-]+", case["case_id"]) is None:
            raise ValueError("invalid benchmark case identifier")
        evidence = PullRequestEvidence.model_validate_json(
            _source(root, case["evidence"]).read_bytes()
        )
        annotation = json.loads(_source(root, case["annotations"]).read_text(encoding="utf-8"))
        criteria = []
        reference_statuses = {}
        for criterion in rubric.criteria:
            label = annotation["criteria"][criterion.criterion_id]
            status = label["status"]
            if status not in {"passed", "failed", "uncertain", "not_assessable", "not_applicable"}:
                raise ValueError("unknown communication reference status")
            normalized_status = status if status in {"passed", "failed"} else "uncertain"
            reference_statuses[criterion.criterion_id] = normalized_status
            criteria.append(
                CriterionAssessment(
                    criterion_id=criterion.criterion_id,
                    status=normalized_status,
                    source="deterministic",
                    explanation=label["explanation"],
                )
            )
        expected = assess_pull_request(
            evidence,
            Policy(),
            rubric,
            _References(
                CriterionBatch(
                    criteria=criteria,
                    provider="reference:synthetic-author-intent",
                )
            ),
        )
        prepared.append((case, evidence, annotation, expected, reference_statuses))
    rows = []
    for case, evidence, annotation, expected, reference_statuses in prepared:
        identifier = case["case_id"]
        run = run_assessment(
            evidence,
            Policy(),
            rubric,
            settings,
            replay=replay_dir / f"{identifier}.tape.json" if replay_dir else None,
            record=record,
        )
        _write_report(
            output_dir / f"{identifier}.assessment.json",
            run.report.model_dump_json(indent=2) + "\n",
        )
        _write_report(
            output_dir / f"{identifier}.provenance.json", json.dumps(run.artifact, indent=2) + "\n"
        )
        if run.tape:
            _write_report(
                output_dir / f"{identifier}.tape.json", run.tape.model_dump_json(indent=2) + "\n"
            )
        actual = {c.criterion_id: c.status for c in run.report.criteria}
        rows.append(
            {
                "case_id": identifier,
                "family": case["family_id"],
                "scenario": annotation["scenario"],
                "expected": expected.verdict,
                "actual": run.report.verdict,
                "criteria": {
                    criterion_id: {
                        "reference": reference_statuses[criterion_id],
                        "actual": actual[criterion_id],
                        "original_status": annotation["criteria"][criterion_id]["status"],
                    }
                    for criterion_id in reference_statuses
                },
            }
        )
    criterion_counts = defaultdict(Counter)
    for row in rows:
        for identifier, values in row["criteria"].items():
            criterion_counts[identifier][f"{values['reference']}->{values['actual']}"] += 1
    summary = {
        "benchmark_version": "communication-v1",
        "reference_version": "author-communication-v1",
        "label_status": "provisional",
        "split": split,
        "support": len(rows),
        "reference_hash": fingerprint([annotation for _, _, annotation, _, _ in prepared]),
        "rubric_hash": fingerprint(rubric.model_dump(mode="json")),
        "split_hash": fingerprint(splits),
        "criterion_confusion": dict(criterion_counts),
        "criterion_agreement": {
            identifier: sum(
                count
                for pair, count in counts.items()
                if pair.split("->")[0] == pair.split("->")[1]
            )
            / len(rows)
            for identifier, counts in criterion_counts.items()
        },
        "decision_agreement": sum(r["expected"] == r["actual"] for r in rows) / len(rows),
        "false_acceptance": sum(
            r["actual"] == "accepted" and r["expected"] != "accepted" for r in rows
        ),
        "unresolved": sum(r["actual"] == "unresolved" for r in rows),
        "by_family": {
            name: _decision_metrics([r for r in rows if r["family"] == name])
            for name in sorted({r["family"] for r in rows})
        },
        "by_scenario": {
            name: _decision_metrics([r for r in rows if r["scenario"] == name])
            for name in sorted({r["scenario"] for r in rows})
        },
        "cases": rows,
    }
    _write_report(output_dir / "summary.json", json.dumps(summary, indent=2) + "\n")
    return summary


def benchmark_main(root: Path) -> None:
    """Run the example benchmark with explicit model selection and fixed family splits.

    :param root: Corpus directory supplied by the example entry point.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", choices=["development", "evaluation", "all"], default="evaluation"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("reports/communication-benchmark"))
    parser.add_argument(
        "--assessment-rubric", type=Path, default=root / "communication/rubric.toml"
    )
    parser.add_argument("--models-config", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--decider")
    parser.add_argument("--model-override", action="append", default=[])
    tapes = parser.add_mutually_exclusive_group()
    tapes.add_argument("--record", action="store_true")
    tapes.add_argument("--replay-dir", type=Path)
    args = parser.parse_args()
    try:
        settings = load_decider_settings(
            resolve_catalog_path(args.models_config),
            profile=args.profile,
            decider=args.decider,
            overrides=args.model_override,
        )
        rubric = load_assessment_rubric(args.assessment_rubric)
        result = run_benchmark(
            root,
            rubric,
            settings,
            args.output_dir,
            split=args.split,
            replay_dir=args.replay_dir,
            record=args.record,
        )
    except (OSError, ValueError, KeyError):
        parser.error("could not run the communication benchmark with these inputs")
    print(json.dumps(result, indent=2))
