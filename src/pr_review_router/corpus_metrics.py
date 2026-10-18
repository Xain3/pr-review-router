"""Summarize corpus predictions without hiding errors in small scenario groups."""

from collections import Counter
from collections.abc import Callable
from typing import Any

Row = dict[str, Any]


def stratify(rows: list[Row], summarize: Callable[[list[Row]], Row]) -> Row:
    """Compute marginals and scenario-by-level intersections with explicit support.

    :param rows: Cases carrying scenario, family, split, and reference triviality metadata.
    :param summarize: Metric calculation applied identically to each nonempty group.
    :returns: Named partitions and scenario-by-reference-level metric groups.
    """
    result: Row = {}
    for dimension in (
        "scenario",
        "family_id",
        "triviality_level",
        "split",
        "author_id",
        "reference_confidence",
    ):
        groups: dict[str, list[Row]] = {}
        for row in rows:
            groups.setdefault(str(row.get(dimension, "unspecified")), []).append(row)
        result[f"by_{dimension}"] = {key: summarize(group) for key, group in sorted(groups.items())}
    intersections: dict[str, dict[str, list[Row]]] = {}
    for row in rows:
        intersections.setdefault(row["scenario"], {}).setdefault(
            row["triviality_level"], []
        ).append(row)
    result["by_scenario_and_triviality_level"] = {
        scenario: {level: summarize(group) for level, group in sorted(levels.items())}
        for scenario, levels in sorted(intersections.items())
    }
    return result


def confusion(pairs: list[tuple[str, str]]) -> Row:
    """Count every reference/prediction pair, including uncertain and unanswered labels.

    :param pairs: Expected and actual categorical labels.
    :returns: Support, agreement, confusion counts, and false-pass counts.
    """
    counts = Counter(f"{expected}->{actual}" for expected, actual in pairs)
    return {
        "support": len(pairs),
        "agreement": sum(expected == actual for expected, actual in pairs) / len(pairs),
        "expected_to_actual": dict(sorted(counts.items())),
        "false_passes": sum(
            expected in {"failed", "uncertain"} and actual == "passed" for expected, actual in pairs
        ),
        "unanswered": sum(actual == "unanswered" for _, actual in pairs),
        "uncertain_references": sum(expected == "uncertain" for expected, _ in pairs),
    }


def ordinal_metrics(rows: list[Row]) -> Row:
    """Score ordinal and binary triviality while keeping abstentions in the denominator.

    :param rows: Cases containing reference and predicted scores; either can be None.
    :returns: Numeric accuracy on known references, label agreement, and full uncertainty confusion.
    """
    known = [row for row in rows if row["reference_score"] is not None]
    unknown = [row for row in rows if row["reference_score"] is None]
    classified = [row for row in rows if row["predicted_score"] is not None]
    paired = [row for row in known if row["predicted_score"] is not None]
    errors = [abs(row["reference_score"] - row["predicted_score"]) for row in paired]
    matrix = [[0 for _ in range(5)] for _ in range(5)]
    for row in paired:
        matrix[row["reference_score"]][row["predicted_score"]] += 1
    return {
        "support": len(rows),
        "reference_classified": len(known),
        "reference_uncertain": len(unknown),
        "classified": len(classified),
        "paired_classified": len(paired),
        "exact_accuracy": errors.count(0) / len(known) if known else None,
        "exact_accuracy_when_classified": errors.count(0) / len(errors) if errors else None,
        "mean_absolute_error_when_classified": sum(errors) / len(errors) if errors else None,
        "binary_accuracy": sum(
            (row["reference_score"] <= 1) == (row["predicted_score"] <= 1) for row in paired
        )
        / len(known)
        if known
        else None,
        "native_label_agreement": sum(
            row.get(
                "reference_label",
                str(row["reference_score"]) if row["reference_score"] is not None else "uncertain",
            )
            == row["predicted_label"]
            for row in rows
        )
        / len(rows),
        "nontrivial_predicted_trivial": sum(
            row["reference_score"] > 1 and row["predicted_score"] <= 1 for row in paired
        ),
        "unresolved_reference_classifications": sum(
            row["predicted_score"] is not None for row in unknown
        ),
        "unresolved_reference_predicted_trivial": sum(
            row["predicted_score"] is not None and row["predicted_score"] <= 1 for row in unknown
        ),
        "reference_score_to_label": dict(
            sorted(
                Counter(
                    f"{row['reference_score'] if row['reference_score'] is not None else 'uncertain'}->{row['predicted_label']}"
                    for row in rows
                ).items()
            )
        ),
        "confusion_matrix": {
            "reference_scores": list(range(5)),
            "predicted_scores": list(range(5)),
            "counts": matrix,
            "uncertain": [
                sum(
                    row["reference_score"] == score and row["predicted_label"] == "uncertain"
                    for row in known
                )
                for score in range(5)
            ],
            "unanswered": [
                sum(
                    row["reference_score"] == score and row["predicted_label"] == "unanswered"
                    for row in known
                )
                for score in range(5)
            ],
            "uncertain_reference": {
                "counts": [
                    sum(row["predicted_score"] == score for row in unknown) for score in range(5)
                ],
                "uncertain": sum(row["predicted_label"] == "uncertain" for row in unknown),
                "unanswered": sum(row["predicted_label"] == "unanswered" for row in unknown),
            },
        },
        "predicted_scores": {row["case_id"]: row["predicted_score"] for row in rows},
    }


def rubric_metrics(rows: list[Row]) -> Row:
    """Summarize criterion and recommendation confusion for one group of cases.

    :param rows: Cases with pinned expected rubric labels and actual rubric results.
    :returns: Support, per-criterion confusion, recommendation risks, and ordinal metrics.
    """
    criteria = sorted({key for row in rows for key in row["expected"]["criteria"]})
    recommendation = confusion(
        [
            (row["expected"]["recommendation"], row["actual"].get("recommendation") or "unanswered")
            for row in rows
        ]
    )
    recommendation["unsafe_approvals"] = sum(
        (
            row["actual"].get("approval_recommended") is True
            or row["actual"].get("recommendation") == "skip_and_approve"
        )
        and row["expected"]["recommendation"] != "skip_and_approve"
        for row in rows
    )
    recommendation["missed_blocks"] = sum(
        row["expected"]["recommendation"] == "block"
        and row["actual"].get("recommendation") in {"skip_and_approve", "suggest_changes"}
        for row in rows
    )
    return {
        "case_count": len(rows),
        "family_count": len({row["family_id"] for row in rows}),
        "criteria": {
            key: {
                **confusion(
                    [
                        (
                            row["expected"]["criteria"][key],
                            row["actual"].get("criteria", {}).get(key) or "unanswered",
                        )
                        for row in rows
                        if key in row["expected"]["criteria"]
                    ]
                ),
                "contested_references": sum(
                    key in row.get("contested_criteria", []) for row in rows
                ),
            }
            for key in criteria
        },
        "recommendation": recommendation,
        "triviality": ordinal_metrics(rows),
    }
