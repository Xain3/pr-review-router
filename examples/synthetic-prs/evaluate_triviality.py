"""Score offline change-triviality predictions against all provisional reference labels."""

import argparse
import json
import runpy
from pathlib import Path

from pr_review_router.corpus_metrics import ordinal_metrics, stratify


def evaluate(
    root: Path, predictions: dict, split: str = "all", reference_revision: str = "latest"
) -> dict:
    """Calculate ordinal and binary accuracy without running inference.

    :param root: Corpus directory with an index and independently stored annotations.
    :param predictions: Version-1 object containing one integer score per case.
    :param split: Selected family-disjoint partition; null scores explicitly abstain.
    :param reference_revision: Latest adjudications by default; original or a named snapshot otherwise.
    :returns: Exact accuracy, mean absolute error, binary accuracy, and confusion counts.
    :raises ValueError: If predictions are incomplete, duplicated, or outside the scale.
    """
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    if split not in {"all", "development", "evaluation"}:
        raise ValueError("Unknown corpus split")
    validation = runpy.run_path(str(Path(__file__).with_name("validate.py")))
    validation["validate_splits"](root.resolve(), index)
    revision = validation["load_grading_references"](root.resolve(), split, reference_revision)
    cases = [case for case in index["cases"] if split == "all" or case["split"] == split]
    if (
        set(predictions) != {"schema_version", "predictions"}
        or type(predictions["schema_version"]) is not int
        or predictions["schema_version"] != 1
    ):
        raise ValueError("Expected a version-1 predictions object")
    if not isinstance(predictions["predictions"], list):
        raise ValueError("Predictions must be a list")
    predicted = {}
    for item in predictions["predictions"]:
        if not isinstance(item, dict) or set(item) != {"case_id", "score"}:
            raise ValueError("Each prediction must contain exactly case_id and score")
        case_id, score = item["case_id"], item["score"]
        if not isinstance(case_id, str) or case_id in predicted:
            raise ValueError("Prediction case IDs must be strings and unique")
        if score is not None and (type(score) is not int or not 0 <= score <= 4):
            raise ValueError("Prediction scores must be integers from 0 through 4 or null")
        predicted[case_id] = score
    if set(predicted) != {case["case_id"] for case in cases}:
        raise ValueError("Predictions must cover exactly all selected corpus case IDs")
    rows = []
    for case in cases:
        reference = revision.cases[case["case_id"]]
        expected = reference["triviality_score"]
        score = predicted[case["case_id"]]
        rows.append(
            {
                **case,
                "author_triviality_level": case["triviality_level"],
                "triviality_level": reference["triviality_level"],
                "reference_score": expected,
                "reference_label": "uncertain" if expected is None else str(expected),
                "predicted_score": score,
                "predicted_label": "uncertain" if score is None else str(score),
            }
        )
    metrics = ordinal_metrics(rows)
    return {
        "schema_version": 3,
        "label_status": index["label_status"],
        "split": split,
        "case_count": len(rows),
        "family_count": len({row["family_id"] for row in rows}),
        "criterion": "change_triviality",
        "grading_reference": revision.metadata,
        "grading_references": revision.cases,
        "exact_score_accuracy": metrics["exact_accuracy"],
        "mean_absolute_error": metrics["mean_absolute_error_when_classified"],
        "binary_triviality_accuracy": metrics["binary_accuracy"],
        "exact_score_accuracy_by_pr_quality": {
            quality: ordinal_metrics([row for row in rows if row["expected_quality"] == quality])[
                "exact_accuracy"
            ]
            for quality in sorted({row["expected_quality"] for row in rows})
        },
        **metrics,
        **stratify(rows, ordinal_metrics),
    }


def main() -> None:
    """Read a predictions file and print benchmark metrics as JSON.

    :raises SystemExit: If the file or prediction schema is invalid.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "predictions", type=Path, help="JSON predictions covering the selected PRs."
    )
    parser.add_argument(
        "--split", choices=["evaluation", "development", "all"], default="evaluation"
    )
    parser.add_argument(
        "--reference-revision",
        default="latest",
        help="Latest adjudications, original author labels, or a registered review snapshot.",
    )
    args = parser.parse_args()
    try:
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
        if not isinstance(predictions, dict):
            raise ValueError("Predictions must be a JSON object")
        result = evaluate(
            Path(__file__).resolve().parent, predictions, args.split, args.reference_revision
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
