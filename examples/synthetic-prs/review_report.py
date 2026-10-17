"""Report blind-label confusion for either corpus partition without provider inference."""

import argparse
import hashlib
import json
import runpy
from pathlib import Path

from pr_review_router.assessment_rubric import (
    CriterionAssessment,
    combine_assessments,
    load_assessment_rubric,
)

ROOT = Path(__file__).resolve().parent


def report(split: str = "evaluation") -> dict:
    """Compare each archived blind pass with provisional author references by scenario.

    :param split: Development, evaluation, or both partitions explicitly.
    :returns: Stratified confusion and adjudication findings for the selected fresh reviews.
    :raises ValueError: If the review archive or supplied case inputs are stale.
    """
    if split not in {"development", "evaluation", "all"}:
        raise ValueError("Unknown corpus split")
    validation = runpy.run_path(str(ROOT / "validate.py"))
    validation["validate"](ROOT)
    archive_paths = {
        partition: ROOT / relative
        for partition, relative in validation["ACTIVE_REVIEW_ARCHIVES"].items()
        if split == "all" or split == partition
    }
    archives = {
        partition: json.loads(path.read_text(encoding="utf-8"))
        for partition, path in archive_paths.items()
    }
    records = {
        case_id: record
        for archive in archives.values()
        for case_id, record in archive["cases"].items()
    }
    corpus_path = ROOT / ("corpus.json" if split == "all" else f"corpus-{split}.json")
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    rubric = load_assessment_rubric(ROOT / "rubric.toml")
    score = runpy.run_path(str(ROOT / "benchmark.py"))["score"]
    results = {}
    for reviewer in ("A", "B"):
        cases = []
        for case_id, record in records.items():
            native = {key: label["reviewers"][reviewer] for key, label in record["labels"].items()}
            statuses = {
                criterion.criterion_id: (
                    criterion.choice_statuses[native[criterion.criterion_id]] or "uncertain"
                    if criterion.criterion_id == "change-triviality"
                    else native[criterion.criterion_id]
                )
                for criterion in rubric.criteria
            }
            advice = combine_assessments(
                rubric,
                [
                    CriterionAssessment(
                        criterion_id=key,
                        status=value,
                        source="deterministic",
                        explanation="Policy projection of archived blind labels, not a provider call.",
                    )
                    for key, value in statuses.items()
                ],
                {"change-triviality": native["change-triviality"]},
            )
            cases.append(
                {
                    "case_id": case_id,
                    "rubric": {
                        "criteria": statuses,
                        "recommendation": advice.recommendation,
                        "native_answers": {
                            "change-triviality": {"choice": native["change-triviality"]}
                        },
                    },
                }
            )
        results[reviewer] = score({"reference_labels": corpus, "cases": cases}, split)
    labels = [
        (case_id, key, label)
        for case_id, record in records.items()
        for key, label in record["labels"].items()
    ]
    unresolved = [
        {
            "case_id": case_id,
            "criterion": key,
            "author": label["oracle"],
            "reviewers": label["reviewers"],
            "adjudicated": label["adjudicated"],
            "rationale": label["rationale"],
        }
        for case_id, key, label in labels
        if label["adjudicated"] == "uncertain"
        and len({label["oracle"], label["adjudicated"], *label["reviewers"].values()}) > 1
    ]
    return {
        "report_version": 2,
        "split": split,
        "kind": "independent_ai_label_review",
        "label_status": "provisional",
        "archives_sha256": {
            partition: hashlib.sha256(path.read_bytes()).hexdigest()
            for partition, path in archive_paths.items()
        },
        "adjudication_summary": {
            "case_count": len(records),
            "label_count": len(labels),
            "contested_labels": sum(label["contested"] for _, _, label in labels),
            "reviewer_disagreements": sum(
                len(set(label["reviewers"].values())) > 1 for _, _, label in labels
            ),
            "unresolved_disagreements": unresolved,
            "author_reference_overrides": [
                {
                    "case_id": case_id,
                    "criterion": key,
                    "author": label["oracle"],
                    "adjudicated": label["adjudicated"],
                    "rationale": label["rationale"],
                }
                for case_id, key, label in labels
                if label["adjudicated"] != label["oracle"]
            ],
        },
        "limitations": "These are actual blind AI label judgments against provisional author intent, "
        "not live provider-quality measurements, human ground truth, or calibration. Recommendation "
        "confusion is a policy projection of those labels, not an observed router recommendation. "
        "All cases, including contested author references, remain in overall denominators; "
        "by_reference_confidence separates contested cases. Final adjudications and unresolved "
        "disagreements are recorded separately; confusion continues to use author-intent references.",
        "reviewers": results,
    }


def main() -> None:
    """Write full scenario, criterion, and triviality confusion under ignored reports/."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", choices=["evaluation", "development", "all"], default="evaluation"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = report(args.split)
    output = args.output or Path(
        "reports/synthetic-prs-label-review.json"
        if args.split == "evaluation"
        else f"reports/synthetic-prs-label-review-{args.split}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "kind": result["kind"],
                "reviewers": {
                    key: {
                        "case_count": value["case_count"],
                        "triviality": value["triviality"]["reference_score_to_label"],
                    }
                    for key, value in result["reviewers"].items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
