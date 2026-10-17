"""Load versioned corpus judgments and project them through the rubric's policy."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .assessment_rubric import (
    CriterionAssessment,
    Status,
    combine_assessments,
    load_assessment_rubric,
)

REGISTRY = "independent-review/reference-revisions.json"


@dataclass(frozen=True)
class ReferenceRevision:
    """Selected grading judgments with explicit source provenance and coverage."""

    cases: dict[str, dict[str, Any]]
    metadata: dict[str, Any]
    archives: dict[str, str]


def _source(root: Path, relative: str) -> Path:
    """Confine a reference source to its corpus directory.

    :param root: Resolved corpus root.
    :param relative: Manifest-relative source path.
    :returns: Confined source path.
    :raises ValueError: If a path is absolute or leaves the corpus.
    """
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"Unsafe reference source: {relative}")
    return path


def reference_revisions(root: Path) -> dict[str, Any]:
    """Read the versioned revision registry without loading model inputs or calling providers.

    :param root: Corpus root containing the independent-review registry.
    :returns: Validated registry with a resolvable latest review revision.
    :raises ValueError: If the registry or latest pointer is invalid.
    """
    registry = json.loads((root / REGISTRY).read_text(encoding="utf-8"))
    if (
        registry.get("schema_version") != 1
        or not isinstance(registry.get("revisions"), dict)
        or registry.get("latest") not in registry["revisions"]
        or registry["revisions"][registry["latest"]].get("kind") != "adjudicated_review"
        or registry["revisions"].get("original", {}).get("kind") != "author_intent"
    ):
        raise ValueError("Invalid reference revision registry")
    return registry


def load_reference_revision(
    root: Path, split: str = "all", revision: str = "latest"
) -> ReferenceRevision:
    """Resolve a grading revision and recompute its expected advisory recommendations.

    :param root: Corpus root with index, rubric, annotations, and review snapshots.
    :param split: Family-disjoint partition, or all cases explicitly.
    :param revision: Latest alias, original author baseline, or registered snapshot name.
    :returns: Selected statuses, native triviality judgments, and source fingerprints.
    :raises ValueError: If a revision is unknown, stale, malformed, or lacks selected cases.
    """
    root = root.resolve()
    registry = reference_revisions(root)
    resolved = registry["latest"] if revision == "latest" else revision
    if resolved not in registry["revisions"]:
        available = ", ".join(["latest", *registry["revisions"]])
        raise ValueError(f"Unknown reference revision {revision!r}; available: {available}")
    if split not in {"all", "development", "evaluation"}:
        raise ValueError("Unknown corpus split")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    selected = [case for case in index["cases"] if split == "all" or case["split"] == split]
    definition = registry["revisions"][resolved]
    kind = definition.get("kind")
    if kind not in {"author_intent", "adjudicated_review"}:
        raise ValueError(f"Invalid reference kind for {resolved}")
    protocol = definition.get("review_protocol")
    if kind == "adjudicated_review" and protocol not in {"historical", "blind_assessments"}:
        raise ValueError(f"Invalid review protocol for {resolved}")
    rubric = load_assessment_rubric(root / "rubric.toml")
    identifiers = {criterion.criterion_id for criterion in rubric.criteria}
    levels = {level["label"]: level["score"] for level in index["triviality_scale"]["levels"]}
    paths = {
        REGISTRY: root / REGISTRY,
        "rubric.toml": root / "rubric.toml",
        "index.json": root / "index.json",
    }
    records: dict[str, Any] = {}
    archives = definition.get("archives", {})
    for partition, relative in archives.items():
        if partition not in {"development", "evaluation"}:
            raise ValueError(f"Invalid reference partition: {partition}")
        path = _source(root, relative)
        archive = json.loads(path.read_text(encoding="utf-8"))
        expected_ids = {case["case_id"] for case in index["cases"] if case["split"] == partition}
        if (
            archive.get("schema_version") != 1
            or archive.get("label_status") != "independent_ai_review"
            or archive.get("rubric_sha256")
            != hashlib.sha256((root / "rubric.toml").read_bytes()).hexdigest()
            or set(archive.get("cases", {})) != expected_ids
        ):
            raise ValueError(f"Stale or incomplete reference archive: {relative}; re-review it")
        records.update(archive["cases"])
        paths[relative] = path
    if kind == "adjudicated_review":
        missing = [case["case_id"] for case in selected if case["case_id"] not in records]
        if missing:
            raise ValueError(
                f"Reference revision {resolved} does not cover {split}: {', '.join(missing)}"
            )
    cases = {}
    labels: dict[str, Any]
    for case in selected:
        case_id = case["case_id"]
        if kind == "author_intent":
            path = _source(root, case["annotations"])
            annotation = json.loads(path.read_text(encoding="utf-8"))
            paths[case["annotations"]] = path
            labels = {
                key: value.get("level")
                or {"not_assessable": "uncertain"}.get(value.get("status"), value.get("status"))
                for key, value in annotation["criteria"].items()
                if key in identifiers
            }
            contested = annotation.get("contested_criteria", [])
        else:
            record = records[case_id]
            digest = hashlib.sha256()
            directory = _source(root, case["evidence"]).parent
            for name in ("evidence.json", "metadata.json", "review.md"):
                path = directory / name
                digest.update(name.encode() + b"\0")
                digest.update(path.read_bytes() if path.exists() else b"")
                digest.update(b"\0")
            if digest.hexdigest() != record.get("input_sha256"):
                raise ValueError(f"{case_id}: stale reference inputs; re-review it")
            labels = {key: value["adjudicated"] for key, value in record["labels"].items()}
            contested = [key for key, value in record["labels"].items() if value["contested"]]
        if set(labels) != identifiers or labels["change-triviality"] not in {*levels, "uncertain"}:
            raise ValueError(f"{case_id}: invalid reference criterion labels")
        assessments = []
        for criterion in rubric.criteria:
            native = labels[criterion.criterion_id]
            if criterion.criterion_id == "change-triviality":
                status = criterion.choice_statuses.get(native) or "uncertain"
            elif native in {"passed", "failed", "uncertain"}:
                status = cast(Status, native)
            else:
                raise ValueError(f"{case_id}/{criterion.criterion_id}: invalid reference status")
            assessments.append(
                CriterionAssessment(
                    criterion_id=criterion.criterion_id,
                    status=status,
                    source="deterministic",
                    explanation="Deterministic policy projection of separately attributed reference judgments.",
                )
            )
        advice = combine_assessments(rubric, assessments, labels)
        native_level = labels["change-triviality"]
        cases[case_id] = {
            "criteria": {item.criterion_id: item.status for item in assessments},
            "recommendation": advice.recommendation,
            "triviality_level": native_level,
            "triviality_score": levels.get(native_level),
            "contested_criteria": contested,
        }
    return ReferenceRevision(
        cases=cases,
        archives=archives,
        metadata={
            "requested": revision,
            "revision": resolved,
            "kind": kind,
            "review_protocol": protocol,
            "label_status": "provisional",
            "case_count": len(cases),
            "description": definition.get("description", ""),
            "sources_sha256": {
                name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()
            },
        },
    )
