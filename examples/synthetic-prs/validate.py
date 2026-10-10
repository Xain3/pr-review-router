"""Validate corpus structure, patch coverage, commit accounting, and label references offline."""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from pr_review_router.config import Policy
from pr_review_router.contracts import PullRequestEvidence
from pr_review_router.engine import assess_coverage, assess_pr_text


def require(condition: bool, message: str) -> None:
    """Reject an invalid fixture without depending on Python assertion settings.

    :param condition: Required invariant.
    :param message: Error identifying the invalid fixture.
    :raises ValueError: If the condition is false.
    """
    if not condition:
        raise ValueError(message)


def read_json(root: Path, relative: str) -> dict:
    """Read a manifest-linked JSON object confined to the corpus directory.

    :param root: Resolved corpus directory.
    :param relative: Manifest-relative path.
    :returns: Decoded JSON object.
    :raises ValueError: If the path leaves the corpus directory or is not an object.
    """
    source = (root / relative).resolve()
    require(
        not Path(relative).is_absolute() and source.is_relative_to(root),
        f"Unsafe corpus path: {relative}",
    )
    value = json.loads(source.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"Not a JSON object: {relative}")
    return value


def validate(root: Path) -> dict:
    """Check all 100 fixtures using router contracts and independent cross-file invariants.

    :param root: Directory containing index.json and prs/.
    :returns: Coverage counts suitable for a developer check or regression test.
    :raises ValueError: If metadata, patches, annotations, or indexes disagree.
    """
    root = root.resolve()
    index = read_json(root, "index.json")
    require(
        index["schema_version"] == 1 and index["synthetic"] is True,
        "Unsupported or non-synthetic corpus",
    )
    require(index["label_status"] == "provisional", "Labels must retain synthetic provenance")
    require(index["case_count"] == len(index["cases"]) == 100, "Expected exactly 100 PRs")
    ids = [case["case_id"] for case in index["cases"]]
    require(len(set(ids)) == 100, "Duplicate case IDs")
    expected_ids = {f"pr-{number:03d}" for number in range(1, 101)}
    require(set(ids) == expected_ids, "Missing or unexpected PR IDs")
    require(
        {path.name for path in (root / "prs").iterdir()} == expected_ids,
        "Unindexed or missing PR directories",
    )
    require(len(index["criteria"]) == 12, "Expected the complete 12-criterion rubric")
    scenarios = Counter()
    qualities = Counter()
    families = Counter()
    family_scenarios = set()
    tags = Counter()
    rationale_complexities = Counter()
    failures = Counter()
    valid_syntax_gibberish = 0
    commit_count = 0
    sha_pattern = re.compile(r"[0-9a-f]{40}")
    table = (root / "INDEX.md").read_text(encoding="utf-8")
    for case in index["cases"]:
        case_id = case["case_id"]
        data = {
            kind: read_json(root, case[kind]) for kind in ("evidence", "metadata", "annotations")
        }
        evidence = PullRequestEvidence.model_validate(data["evidence"])
        metadata, labels = data["metadata"], data["annotations"]
        require(
            set(data["evidence"]) == set(PullRequestEvidence.model_fields),
            f"{case_id}: missing or unexpected evidence fields",
        )
        require(evidence.number == int(case_id.removeprefix("pr-")), f"{case_id}: wrong PR number")
        require(
            metadata["schema_version"] == labels["schema_version"] == 1,
            f"{case_id}: unknown companion schema",
        )
        require(metadata["synthetic"] is True, f"{case_id}: missing synthetic marker")
        require(metadata["case_id"] == labels["case_id"] == case_id, f"{case_id}: mismatched IDs")
        require(
            labels["label_status"] == "provisional"
            and labels["label_origin"] == "synthetic_author_intent",
            f"{case_id}: missing label provenance",
        )
        for key in ("family_id", "scenario", "variant", "complexity", "expected_quality", "tags"):
            require(case[key] == labels[key], f"{case_id}: index disagrees on {key}")
        require(case["title"] == evidence.title, f"{case_id}: stale title index")
        require(
            f"[{case_id}]({case['evidence']})" in table, f"{case_id}: missing Markdown index link"
        )
        require(
            assess_coverage(evidence, Policy()).complete, f"{case_id}: incomplete or invalid patch"
        )
        require(
            set(labels["criteria"]) == set(index["criteria"]),
            f"{case_id}: incomplete rubric labels",
        )
        for key, criterion in labels["criteria"].items():
            require(
                criterion["status"] in {"passed", "failed", "not_assessable", "not_applicable"},
                f"{case_id}: invalid status for {key}",
            )
            require(
                bool(criterion["explanation"].strip()) and bool(criterion["sources"]),
                f"{case_id}: missing explanation or evidence references for {key}",
            )
            for reference in criterion["sources"]:
                filename, pointer = reference.split("#", 1)
                require(
                    filename in {"evidence.json", "metadata.json"} and pointer.startswith("/"),
                    f"{case_id}: invalid source {reference}",
                )
                value = data[filename.removesuffix(".json")]
                for token in pointer[1:].split("/"):
                    token = token.replace("~1", "/").replace("~0", "~")
                    value = value[int(token)] if isinstance(value, list) else value[token]
        failed = sorted(
            key for key, criterion in labels["criteria"].items() if criterion["status"] == "failed"
        )
        require(failed == case["failed_criteria"], f"{case_id}: stale failure index")
        require(
            labels["expected_quality"] == ("needs_revision" if failed else "good"),
            f"{case_id}: quality label disagrees with criteria",
        )
        if labels["expected_quality"] == "good":
            require(
                all(
                    item["status"] in {"passed", "not_applicable"}
                    for item in labels["criteria"].values()
                ),
                f"{case_id}: good case contains unassessed criteria",
            )
        title_valid = assess_pr_text(evidence, Policy(title_format="conventional_commit")).valid
        require(
            title_valid == (labels["criteria"]["title_format"]["status"] == "passed"),
            f"{case_id}: title-format oracle disagrees with router",
        )
        require(
            labels["criteria"]["breaking_changes"]["status"] != "not_applicable"
            if labels["has_breaking_change"]
            else labels["criteria"]["breaking_changes"]["status"] == "not_applicable",
            f"{case_id}: breaking-change applicability disagrees",
        )
        require(
            metadata["base_sha"] == evidence.base_sha and metadata["head_sha"] == evidence.head_sha,
            f"{case_id}: SHA metadata disagrees",
        )
        require(metadata["changed_files"] == len(evidence.files), f"{case_id}: wrong file count")
        require(
            metadata["additions"] == sum(file.additions for file in evidence.files)
            and metadata["deletions"] == sum(file.deletions for file in evidence.files),
            f"{case_id}: aggregate diff counts disagree",
        )
        require(
            metadata["author"]["email"].endswith("@example.invalid")
            and metadata["url"].startswith("https://example.invalid/"),
            f"{case_id}: non-fictional identity or URL",
        )
        require(bool(metadata["commits"]), f"{case_id}: empty commit list")
        parent = evidence.base_sha
        paths = []
        file_map = {file.path: file for file in evidence.files}
        previous_time = None
        for commit in metadata["commits"]:
            require(
                bool(sha_pattern.fullmatch(commit["sha"])) and commit["parents"] == [parent],
                f"{case_id}: invalid linear commit chain",
            )
            require(bool(commit["message"].strip()), f"{case_id}: empty commit message")
            require(commit["author"] == metadata["author"], f"{case_id}: commit author disagrees")
            require(
                len(commit["files"]) == 1 and commit["files"][0] in file_map,
                f"{case_id}: unexpected commit file assignment",
            )
            file = file_map[commit["files"][0]]
            require(
                (commit["additions"], commit["deletions"]) == (file.additions, file.deletions),
                f"{case_id}: commit and patch line counts disagree",
            )
            authored = datetime.fromisoformat(commit["authored_at"])
            require(
                authored <= datetime.fromisoformat(metadata["created_at"])
                and (previous_time is None or authored >= previous_time),
                f"{case_id}: inconsistent commit timestamps",
            )
            previous_time = authored
            parent = commit["sha"]
            paths.extend(commit["files"])
        require(
            parent == evidence.head_sha and paths == [file.path for file in evidence.files],
            f"{case_id}: commit head or file coverage disagrees",
        )
        require(
            datetime.fromisoformat(metadata["created_at"])
            <= datetime.fromisoformat(metadata["updated_at"]),
            f"{case_id}: invalid PR timestamps",
        )
        scenarios[labels["scenario"]] += 1
        qualities[labels["expected_quality"]] += 1
        families[labels["family_id"]] += 1
        family_scenarios.add((labels["family_id"], labels["scenario"]))
        tags.update(labels["tags"])
        failures.update(failed)
        commit_count += len(metadata["commits"])
        if labels["scenario"] == "missing_rationale":
            rationale_complexities[labels["complexity"]] += 1
        if "gibberish_title" in labels["tags"] and title_valid:
            valid_syntax_gibberish += 1
    require(
        dict(scenarios) == index["scenario_counts"] and set(scenarios.values()) == {10},
        "Expected ten cases in each of ten scenarios",
    )
    require(
        dict(qualities) == index["quality_counts"] == {"good": 10, "needs_revision": 90},
        "Expected ten good PRs and ninety PRs needing revision",
    )
    require(len(families) == 10 and set(families.values()) == {10}, "Unbalanced change families")
    require(len(family_scenarios) == 100, "Duplicate family/scenario pairs")
    require(
        rationale_complexities == {"trivial": 3, "non_trivial": 7},
        "Missing trivial or non-trivial rationale cases",
    )
    require(
        valid_syntax_gibberish >= 5 and tags["undocumented_breaking_change"] >= 2,
        "Missing semantic-title or breaking-change edge cases",
    )
    return {
        "case_count": len(ids),
        "commit_count": commit_count,
        "scenario_counts": dict(scenarios),
        "quality_counts": dict(qualities),
        "failed_criterion_counts": dict(failures),
        "valid_syntax_gibberish": valid_syntax_gibberish,
    }


if __name__ == "__main__":
    print(json.dumps(validate(Path(__file__).resolve().parent), indent=2))
