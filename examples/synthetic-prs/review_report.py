"""Report held-out blind-label confusion offline, separately from provider evaluation."""

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


def report() -> dict:
    """Compare each archived blind pass with provisional author references by scenario.

    :returns: Stratified confusion for both actual independent AI label-review passes.
    :raises ValueError: If the review archive or supplied case inputs are stale.
    """
    runpy.run_path(str(ROOT / "validate.py"))["validate"](ROOT)
    archive_path = ROOT / "independent-review" / "authored-reviews.json"
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    corpus = json.loads((ROOT / "corpus-evaluation.json").read_text(encoding="utf-8"))
    rubric = load_assessment_rubric(ROOT / "rubric.toml")
    score = runpy.run_path(str(ROOT / "benchmark.py"))["score"]
    results = {}
    for reviewer in ("A", "B"):
        cases = []
        for case_id, record in archive["cases"].items():
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
        results[reviewer] = score({"reference_labels": corpus, "cases": cases}, "evaluation")
    return {
        "report_version": 1,
        "kind": "independent_ai_label_review",
        "label_status": "provisional",
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "limitations": "These are actual blind AI label judgments against provisional author intent, "
        "not live provider-quality measurements, human ground truth, or calibration. Recommendation "
        "confusion is a policy projection of those labels, not an observed router recommendation. "
        "All cases, including contested author references, remain in overall denominators; "
        "by_reference_confidence separates contested cases. Six triviality adjudications remain uncertain.",
        "reviewers": results,
    }


def main() -> None:
    """Write full scenario, criterion, and triviality confusion under ignored reports/."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("reports/synthetic-prs-label-review.json")
    )
    args = parser.parse_args()
    result = report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
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
