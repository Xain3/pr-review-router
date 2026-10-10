"""Score offline change-triviality predictions against all 100 provisional reference labels."""

import argparse
import json
from collections import Counter
from pathlib import Path


def evaluate(root: Path, predictions: dict) -> dict:
    """Calculate ordinal and binary accuracy without running inference.

    :param root: Corpus directory with an index and independently stored annotations.
    :param predictions: Version-1 object containing one integer score per case.
    :returns: Exact accuracy, mean absolute error, binary accuracy, and confusion counts.
    :raises ValueError: If predictions are incomplete, duplicated, or outside the scale.
    """
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
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
        if type(score) is not int or not 0 <= score <= 4:
            raise ValueError("Prediction scores must be integers from 0 through 4")
        predicted[case_id] = score
    if set(predicted) != {case["case_id"] for case in index["cases"]}:
        raise ValueError("Predictions must cover exactly all corpus case IDs")
    errors = []
    matrix = [[0 for _ in range(5)] for _ in range(5)]
    binary_matches = 0
    matches_by_quality = Counter()
    counts_by_quality = Counter()
    for case in index["cases"]:
        annotation = json.loads((root / case["annotations"]).read_text(encoding="utf-8"))
        expected = annotation["criteria"]["change_triviality"]["score"]
        score = predicted[case["case_id"]]
        errors.append(abs(score - expected))
        matrix[expected][score] += 1
        binary_matches += (score <= 1) == (expected <= 1)
        quality = annotation["expected_quality"]
        counts_by_quality[quality] += 1
        matches_by_quality[quality] += score == expected
    count = len(errors)
    return {
        "schema_version": 1,
        "label_status": index["label_status"],
        "case_count": count,
        "criterion": "change_triviality",
        "exact_score_accuracy": errors.count(0) / count,
        "mean_absolute_error": sum(errors) / count,
        "binary_triviality_accuracy": binary_matches / count,
        "exact_score_accuracy_by_pr_quality": {
            quality: matches_by_quality[quality] / total
            for quality, total in counts_by_quality.items()
        },
        "confusion_matrix": {
            "reference_scores": list(range(5)),
            "predicted_scores": list(range(5)),
            "counts": matrix,
        },
    }


def main() -> None:
    """Read a predictions file and print benchmark metrics as JSON.

    :raises SystemExit: If the file or prediction schema is invalid.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path, help="JSON predictions covering all 100 PRs.")
    args = parser.parse_args()
    try:
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
        if not isinstance(predictions, dict):
            raise ValueError("Predictions must be a JSON object")
        result = evaluate(Path(__file__).resolve().parent, predictions)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
