"""Validate corpus structure, patch coverage, commit accounting, and label references offline."""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from pr_review_router.config import Policy
from pr_review_router.contracts import PullRequestEvidence
from pr_review_router.corpus_references import load_reference_revision
from pr_review_router.engine import assess_coverage, assess_pr_text

BASE_CASES = 100
LEGACY_CASE_COUNT = 125
EVALUATION_CASE_COUNT = 24
CASE_COUNT = 157
REVIEWED_CRITERIA = [
    "title-format",
    "summary-section",
    "change-rationale",
    "description-coverage",
    "review-coverage",
    "testing-explanation",
    "change-triviality",
]
ACTIVE_REVIEW_ARCHIVES = {
    "development": "independent-review/development-reviews-2026-10-18-rubric-refresh.json",
    "evaluation": "independent-review/evaluation-reviews-2026-10-18-rubric-refresh.json",
}
REVIEW_STATUSES = {
    "none": "not_assessable",
    "ambiguous": "not_assessable",
    "complete": "passed",
    "open_concern": "passed",
    "partial": "failed",
    "off_target": "failed",
    "generic": "failed",
}
EXTENSION_SCENARIOS = {"what_not_why": 10, "omitted_change": 7, "boundary": 16}
# Reference levels of the matched boundary pairs, adjacent levels differing by one.
BOUNDARY_SCORES = {
    "comment-typo": 0,
    "rename-local": 1,
    "extract-constant": 1,
    "change-default": 2,
    "strip-input": 2,
    "escape-output": 3,
    "optional-param": 3,
    "required-param": 4,
}
# Boundary text must state facts without characterizing impact or naming a level.
BOUNDARY_LEAKS = re.compile(
    r"break|compatib|no behav|trivial|refactor|mechanical|substantive|editorial|"
    r"security|migrat|public",
    re.IGNORECASE,
)


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


def review_input_sha256(directory: Path) -> str:
    """Hash the files an independent reviewer saw, so edited cases invalidate their reviews.

    :param directory: One case directory under ``prs/``.
    :returns: Hexadecimal digest of the evidence, commit metadata, and optional review text.
    """
    digest = hashlib.sha256()
    for name in ("evidence.json", "metadata.json", "review.md"):
        path = directory / name
        digest.update(name.encode() + b"\0")
        digest.update(path.read_bytes() if path.exists() else b"")
        digest.update(b"\0")
    return digest.hexdigest()


def validate_independent_review(
    root: Path,
    index: dict,
    relative: str = "independent-review/reviews.json",
    *,
    check_oracle: bool = True,
    require_blind_assessments: bool | None = None,
    expected_rubric_sha256: str | None = None,
) -> dict:
    """Check that independent-review labels are current, complete, and grounded in evidence.

    :param root: Resolved corpus directory.
    :param index: Validated corpus manifest.
    :param relative: Review archive whose declared scope must match the supplied cases.
    :param check_oracle: Whether archived author labels must match current author annotations.
    :param require_blind_assessments: Require raw blind judgments; registered revisions declare this protocol.
    :param expected_rubric_sha256: Rubric hash pinned by the review revision; defaults to the current rubric.
    :returns: Agreement counts for the independent AI review.
    :raises ValueError: If a label is stale, malformed, or points at missing evidence.
    """
    review = read_json(root, relative)
    if require_blind_assessments is None:
        require_blind_assessments = relative != "independent-review/reviews.json"
    has_previous_adjudications = any(
        "previous_adjudicated" in label
        for record in review.get("cases", {}).values()
        for label in record.get("labels", {}).values()
    )
    requires_history = (
        relative == "independent-review/legacy-reviews.json" or has_previous_adjudications
    )
    require(
        not requires_history or "historical_review_sha256" in review,
        "Review with historical adjudications must declare its historical archive hash",
    )
    historical = (
        read_json(root, "independent-review/reviews.json")
        if "historical_review_sha256" in review
        else None
    )
    require(
        review["schema_version"] == 1 and review["label_status"] == "independent_ai_review",
        "Independent review must not claim human review",
    )
    expected_rubric = (
        expected_rubric_sha256 or hashlib.sha256((root / "rubric.toml").read_bytes()).hexdigest()
    )
    require(
        review["rubric_sha256"] == expected_rubric,
        "Independent review predates the current rubric; re-review the cases",
    )
    require(review["criteria"] == REVIEWED_CRITERIA, "Unexpected independently reviewed criteria")
    if historical is not None:
        require(
            review["historical_review_sha256"]
            == hashlib.sha256((root / "independent-review/reviews.json").read_bytes()).hexdigest(),
            "Legacy re-review no longer matches its preserved historical archive",
        )
    require(
        set(review["cases"]) == {case["case_id"] for case in index["cases"]},
        "Independent review must cover exactly the corpus cases",
    )
    levels = {level["label"] for level in index["triviality_scale"]["levels"]}
    statuses = {"passed", "failed", "uncertain"}
    status_to_oracle = {"passed": "passed", "failed": "failed", "not_assessable": "uncertain"}
    counts = Counter()
    per_criterion = {criterion: Counter() for criterion in REVIEWED_CRITERIA}
    family_triviality = {}
    for case in index["cases"]:
        case_id = case["case_id"]
        directory = root / "prs" / case_id
        record = review["cases"][case_id]
        require(
            record["input_sha256"] == review_input_sha256(directory),
            f"{case_id}: case changed after its independent review; re-review it",
        )
        require(record["round"] in {1, 2, 3}, f"{case_id}: unknown review round")
        evidence = read_json(root, case["evidence"])
        metadata = read_json(root, case["metadata"])
        oracle = read_json(root, case["annotations"])["criteria"]
        sections = set(re.findall(r"^## (.+)$", evidence["body"], re.MULTILINE))
        paths = {file["path"] for file in evidence["files"]}
        require(set(record["labels"]) == set(REVIEWED_CRITERIA), f"{case_id}: missing criteria")
        for criterion, label in record["labels"].items():
            if criterion == "change-triviality":
                require(
                    family_triviality.setdefault(case["family_id"], label["adjudicated"])
                    == label["adjudicated"],
                    f"{case_id}: final triviality differs across matched family patches",
                )
            if historical is not None and case_id in historical["cases"]:
                require(
                    label.get("previous_adjudicated")
                    == historical["cases"][case_id]["labels"][criterion]["adjudicated"],
                    f"{case_id}/{criterion}: historical adjudication provenance disagrees",
                )
            else:
                require(
                    "previous_adjudicated" not in label,
                    f"{case_id}/{criterion}: invented historical adjudication provenance",
                )
            allowed = levels | {"uncertain"} if criterion == "change-triviality" else statuses
            reference = oracle[criterion]
            expected = (
                reference["level"]
                if "level" in reference
                else status_to_oracle[reference["status"]]
            )
            values = [label["oracle"], label["adjudicated"], *label["reviewers"].values()]
            require(
                set(label["reviewers"]) == {"A", "B"} and set(values) <= allowed,
                f"{case_id}/{criterion}: invalid label values",
            )
            if check_oracle:
                require(label["oracle"] == expected, f"{case_id}/{criterion}: stale oracle copy")
            require(
                type(label["contested"]) is bool, f"{case_id}/{criterion}: invalid contested flag"
            )
            if require_blind_assessments or "reviewer_assessments" in label:
                assessments = label.get("reviewer_assessments", {})
                require(
                    set(assessments) == {"A", "B"},
                    f"{case_id}/{criterion}: missing blind judgments",
                )
                for reviewer, assessment in assessments.items():
                    require(
                        assessment["label"] == label["reviewers"][reviewer]
                        and bool(assessment["rationale"].strip())
                        and bool(assessment["evidence"])
                        and set(assessment["evidence"]) <= set(label["evidence"])
                        and type(assessment["contested"]) is bool
                        and (not assessment["contested"] or label["contested"]),
                        f"{case_id}/{criterion}: inconsistent blind judgment for {reviewer}",
                    )
            unanimous = len(set(values)) == 1
            require(
                label["resolution"]
                == (
                    "unanimous"
                    if unanimous
                    else "split_oracle_upheld"
                    if label["adjudicated"] == label["oracle"]
                    else "oracle_overruled"
                ),
                f"{case_id}/{criterion}: resolution disagrees with the labels",
            )
            require(
                unanimous or (label["contested"] is True and bool(label.get("dissent"))),
                f"{case_id}/{criterion}: split labels must be contested with a recorded dissent",
            )
            require(
                bool(label["rationale"].strip()) and bool(label["evidence"]),
                f"{case_id}/{criterion}: missing rationale or evidence",
            )
            for pointer in label["evidence"]:
                parsed = re.fullmatch(r"(body#|commit:|patch:)(.+)", pointer)
                kind, name = (parsed.group(1), parsed.group(2)) if parsed else ("", "")
                require(
                    pointer in {"title", "body", "none"}
                    or (pointer == "review" and (directory / "review.md").exists())
                    or (kind == "body#" and name in sections)
                    or (
                        kind == "commit:"
                        and name.isdigit()
                        and 0 < int(name) <= len(metadata["commits"])
                    )
                    or (kind == "patch:" and name in paths),
                    f"{case_id}/{criterion}: unresolvable evidence {pointer}",
                )
            per_criterion[criterion][label["resolution"]] += 1
            counts["labels"] += 1
            counts[label["resolution"]] += 1
            counts["contested"] += label["contested"]
    require(
        review["summary"]
        == {
            criterion: {
                "unanimous": tally["unanimous"],
                "split": tally["split_oracle_upheld"] + tally["oracle_overruled"],
                "contested": sum(
                    item["labels"][criterion]["contested"] for item in review["cases"].values()
                ),
            }
            for criterion, tally in per_criterion.items()
        },
        "Stale independent-review summary",
    )
    if "preserved_review_sha256" in review:
        preserved_path = "independent-review/legacy-reviews.json"
        preserved = read_json(root, preserved_path)
        require(
            review["preserved_review_sha256"]
            == hashlib.sha256((root / preserved_path).read_bytes()).hexdigest()
            and all(
                review["cases"].get(case_id) == record
                for case_id, record in preserved["cases"].items()
            ),
            "Preserved blind assessments changed; re-review provenance",
        )
    return dict(counts)


def load_active_reviews(root: Path, index: dict) -> dict:
    """Load current reviews only after validating their input and judgment provenance.

    :param root: Corpus root containing both current review archives and preserved history.
    :param index: Manifest with the case assignments for each partition.
    :returns: Case records from the latest validated review of each partition.
    :raises ValueError: If an archive is stale, incomplete, or inconsistent.
    """
    records = {}
    for split, relative in ACTIVE_REVIEW_ARCHIVES.items():
        selected = {**index, "cases": [case for case in index["cases"] if case["split"] == split]}
        validate_independent_review(root, selected, relative)
        records.update(read_json(root, relative)["cases"])
    return records


def load_grading_references(root: Path, split: str, revision: str = "latest"):
    """Validate a registered grading snapshot without requiring its old author labels to be current.

    :param root: Resolved corpus root containing the revision registry.
    :param split: Selected partition or all cases.
    :param revision: Latest, original, or a registered review identifier.
    :returns: Policy-projected references with selected source fingerprints.
    """
    references = load_reference_revision(root, split, revision)
    index = read_json(root, "index.json")
    for partition, relative in references.archives.items():
        scope = references.metadata["case_ids"][partition]
        subset = {**index, "cases": [case for case in index["cases"] if case["case_id"] in scope]}
        validate_independent_review(
            root,
            subset,
            relative,
            check_oracle=False,
            require_blind_assessments=references.metadata["review_protocol"] == "blind_assessments",
            expected_rubric_sha256=references.metadata["rubric_sha256"],
        )
    return references


def validate_splits(root: Path, index: dict) -> dict:
    """Reject family overlap, duplicated patches across partitions, and stale split corpora.

    :param root: Corpus root containing the fixed partition manifest.
    :param index: Validated case index with explicit author and split provenance.
    :returns: Case and family support for both partitions.
    :raises ValueError: If assignments leak a family or patch, or omit a selected input.
    """
    splits = read_json(root, "splits.json")
    require(
        splits["schema_version"] == 1 and splits["unit"] == "family_id", "Invalid split manifest"
    )
    require(set(splits["partitions"]) == {"development", "evaluation"}, "Invalid partitions")
    corpus = read_json(root, "corpus.json")
    require(corpus.get("assessment_rubric") == "rubric.toml", "Unexpected execution rubric")
    require(
        [case["case_id"] for case in corpus["cases"]]
        == [case["case_id"] for case in index["cases"]],
        "Execution corpus case order differs from index",
    )
    for execution, indexed in zip(corpus["cases"], index["cases"], strict=True):
        require(
            execution.get("category") == "rubric"
            and execution.get("evidence") == indexed["evidence"]
            and execution.get("review") == indexed.get("review"),
            f"{indexed['case_id']}: execution corpus inputs differ from index",
        )
    families = {}
    patches = {}
    family_patches = {}
    support = {}
    for split in ("development", "evaluation"):
        cases = [case for case in index["cases"] if case["split"] == split]
        require(bool(cases), f"Empty {split} partition")
        ids = [case["case_id"] for case in cases]
        partition = splits["partitions"][split]
        require(
            partition
            == {"case_ids": ids, "family_ids": sorted({case["family_id"] for case in cases})},
            f"Stale {split} partition",
        )
        expected = {**corpus, "cases": [case for case in corpus["cases"] if case["case_id"] in ids]}
        require(read_json(root, f"corpus-{split}.json") == expected, f"Stale {split} corpus")
        for case in cases:
            require(
                families.setdefault(case["family_id"], split) == split, "Family overlaps partitions"
            )
            evidence = read_json(root, case["evidence"])
            fingerprint = hashlib.sha256(
                json.dumps(
                    sorted(evidence["files"], key=lambda file: file["path"]), sort_keys=True
                ).encode()
            ).hexdigest()
            require(
                patches.setdefault(fingerprint, split) == split,
                "Identical patch overlaps partitions",
            )
            require(
                family_patches.setdefault(case["family_id"], fingerprint) == fingerprint,
                f"{case['family_id']}: matched family patches differ",
            )
            number = int(case["case_id"].removeprefix("pr-"))
            held_out = LEGACY_CASE_COUNT < number <= LEGACY_CASE_COUNT + EVALUATION_CASE_COUNT
            require((split == "evaluation") == held_out, "Fixed held-out family assignment changed")
            if held_out:
                labels = read_json(root, case["annotations"])
                require(
                    labels["authorship"]["method"] == "independent_agent_authored"
                    and labels["authorship"]["author_id"] == case["author_id"],
                    f"{case['case_id']}: missing independent author provenance",
                )
        support[split] = {"case_count": len(cases), "family_count": len(partition["family_ids"])}
        if split == "evaluation":
            require(
                len(partition["family_ids"]) == 12
                and set(Counter(case["family_id"] for case in cases).values()) == {2}
                and {case["triviality_score"] for case in cases} == set(range(5)),
                "Evaluation must hold out twelve paired families spanning every triviality level",
            )
    require(
        support
        == {
            "development": {"case_count": 133, "family_count": 18},
            "evaluation": {"case_count": EVALUATION_CASE_COUNT, "family_count": 12},
        },
        "Expected 133 development cases and 24 evaluation cases in disjoint families",
    )
    require(
        len(families) == 30
        and min(Counter(case["family_id"] for case in index["cases"]).values()) >= 2,
        "Every one of thirty families must support at least two unique cases",
    )
    return support


def validate(root: Path) -> dict:
    """Check all fixtures using router contracts and independent cross-file invariants.

    :param root: Directory containing index.json and prs/.
    :returns: Coverage counts suitable for a developer check or regression test.
    :raises ValueError: If metadata, patches, annotations, reviews, or indexes disagree.
    """
    root = root.resolve()
    index = read_json(root, "index.json")
    review_registry = read_json(root, "independent-review/reference-revisions.json")
    router_corpus = {case["case_id"]: case for case in read_json(root, "corpus.json")["cases"]}
    require(
        index["schema_version"] == 3 and index["synthetic"] is True,
        "Unsupported or non-synthetic corpus",
    )
    require(index["label_status"] == "provisional", "Labels must retain synthetic provenance")
    require(
        index["case_count"] == len(index["cases"]) == CASE_COUNT,
        f"Expected exactly {CASE_COUNT} PRs",
    )
    ids = [case["case_id"] for case in index["cases"]]
    require(len(set(ids)) == CASE_COUNT, "Duplicate case IDs")
    expected_ids = {f"pr-{number:03d}" for number in range(1, CASE_COUNT + 1)}
    require(set(ids) == expected_ids, "Missing or unexpected PR IDs")
    require(set(router_corpus) == expected_ids, "Router corpus and index list different PRs")
    require(
        {path.name for path in (root / "prs").iterdir()} == expected_ids,
        "Unindexed or missing PR directories",
    )
    require(
        len(index["criteria"]) == 20
        and {"change_triviality", "summary-section", "review-coverage"} <= set(index["criteria"])
        and set(index["rubric_label_mirrors"]) <= set(index["criteria"])
        and set(index["rubric_label_mirrors"].values()) <= set(index["criteria"]),
        "Expected fourteen status-based criteria and an ordinal change-triviality criterion",
    )
    scale = index["triviality_scale"]
    require(
        scale["minimum_score"] == 0
        and scale["maximum_score"] == 4
        and scale["direction"] == "higher_scores_are_less_trivial"
        and scale["trivial_scores"] == [0, 1],
        "Invalid triviality scale",
    )
    require(
        [level["score"] for level in scale["levels"]] == list(range(5))
        and len({level["label"] for level in scale["levels"]}) == 5
        and all(level["description"].strip() for level in scale["levels"]),
        "Triviality scale must define all five distinct ordered levels",
    )
    scenarios = Counter()
    qualities = Counter()
    base_scenarios = Counter()
    base_qualities = Counter()
    base_triviality = Counter()
    base_complexity = Counter()
    review_variants = Counter()
    review_coverages = Counter()
    families = Counter()
    family_scenarios = set()
    tags = Counter()
    rationale_complexities = Counter()
    failures = Counter()
    triviality_counts = Counter()
    complexity_counts = Counter()
    text_relationship_counts = Counter()
    text_confounder_count = 0
    family_scores = {}
    valid_syntax_gibberish = 0
    commit_count = 0
    sha_pattern = re.compile(r"[0-9a-f]{40}")
    table = (root / "INDEX.md").read_text(encoding="utf-8")
    for case in index["cases"]:
        case_id = case["case_id"]
        base = int(case_id.removeprefix("pr-")) <= BASE_CASES
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
            metadata["schema_version"] == 1 and labels["schema_version"] == 3,
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
        for mirror, canonical in index["rubric_label_mirrors"].items():
            require(
                labels["criteria"][mirror] == labels["criteria"][canonical],
                f"{case_id}: rubric label mirror disagrees for {mirror}",
            )
        summary_check = assess_pr_text(
            evidence, Policy(title_format="any", required_body_sections=["Summary"])
        )
        require(
            labels["criteria"]["summary-section"]["status"]
            == ("passed" if summary_check.valid else "failed"),
            f"{case_id}: summary-section oracle disagrees with router",
        )
        review_coverage = labels["criteria"]["review-coverage"]
        review_variant = labels["review_variant"]
        review_file = root / case["annotations"]
        review_file = review_file.with_name("review.md")
        has_review = review_variant != "none"
        require(
            review_variant in REVIEW_STATUSES
            and case["review_variant"] == review_variant
            and review_coverage["status"] == REVIEW_STATUSES[review_variant]
            and case["review_coverage"] == review_coverage["status"],
            f"{case_id}: review variant, label, and index disagree",
        )
        require(
            review_file.is_file() == has_review
            and case["review"] == (f"prs/{case_id}/review.md" if has_review else None),
            f"{case_id}: review file does not match its variant",
        )
        require(
            review_coverage["sources"]
            == (["review.md", "evidence.json#/files"] if has_review else []),
            f"{case_id}: review coverage sources disagree with the supplied review",
        )
        router_case = router_corpus[case_id]
        require(
            router_case.get("review") == case["review"]
            or (not has_review and "review" not in router_case),
            f"{case_id}: router corpus review path disagrees",
        )
        require(
            router_case["expectations"]["rubric"]["criteria"]["review-coverage"]
            == {"passed": "passed", "failed": "failed", "not_assessable": "uncertain"}[
                review_coverage["status"]
            ],
            f"{case_id}: router review-coverage label disagrees",
        )
        if has_review:
            require(
                bool(review_file.read_text(encoding="utf-8").strip()), f"{case_id}: empty review"
            )
        review_variants[review_variant] += 1
        review_coverages[review_coverage["status"]] += 1
        triviality = labels["criteria"]["change_triviality"]
        require(
            set(triviality) == {"type", "score", "level", "is_trivial", "explanation", "sources"}
            and triviality["type"] == "ordinal",
            f"{case_id}: invalid ordinal criterion",
        )
        score = triviality["score"]
        require(type(score) is int and 0 <= score <= 4, f"{case_id}: invalid triviality score")
        require(
            type(triviality["is_trivial"]) is bool
            and triviality["is_trivial"] == (score <= 1)
            and triviality["level"] == scale["levels"][score]["label"],
            f"{case_id}: triviality classification disagrees with the scale",
        )
        require(
            labels["complexity"] == ("trivial" if score <= 1 else "non_trivial"),
            f"{case_id}: existing complexity label disagrees with triviality",
        )
        require(
            case["triviality_score"] == score
            and case["triviality_level"] == triviality["level"]
            and type(case["is_trivial"]) is bool
            and case["is_trivial"] == triviality["is_trivial"],
            f"{case_id}: stale triviality index",
        )
        require(
            triviality["sources"] == ["evidence.json#/files"],
            f"{case_id}: triviality must be grounded in the actual diff",
        )
        family_id = labels["family_id"]
        require(
            family_scores.setdefault(family_id, score) == score,
            f"{case_id}: matched diffs have inconsistent triviality scores",
        )
        row = next(
            line
            for line in table.splitlines()
            if line.startswith(f"| [{case_id}]({case['evidence']}) |")
        )
        require(
            f"| {score} ({triviality['level']}) | "
            f"{'yes' if triviality['is_trivial'] else 'no'} |" in row,
            f"{case_id}: missing Markdown triviality score",
        )
        diagnostic = labels["diagnostics"]["text_diff_triviality"]
        require(
            set(diagnostic)
            == {
                "diff_score",
                "title_score",
                "body_score",
                "text_score",
                "assessment",
                "relationship",
                "score_delta",
                "potential_confounder",
                "title_explanation",
                "body_explanation",
                "sources",
            },
            f"{case_id}: invalid text/diff diagnostic shape",
        )
        require(
            type(diagnostic["diff_score"]) is int and diagnostic["diff_score"] == score,
            f"{case_id}: diagnostic disagrees with reference triviality",
        )
        for key in ("title_score", "body_score", "text_score"):
            require(
                diagnostic[key] is None
                or (type(diagnostic[key]) is int and 0 <= diagnostic[key] <= 4),
                f"{case_id}: invalid projected text score",
            )
        signals = [
            diagnostic[key] for key in ("title_score", "body_score") if diagnostic[key] is not None
        ]
        if not signals:
            expected_score, expected_assessment, expected_relationship = (
                None,
                "not_assessable",
                "not_assessable",
            )
        elif len(set(signals)) > 1:
            expected_score, expected_assessment, expected_relationship = (
                None,
                "conflicting",
                "conflicting_text",
            )
        else:
            expected_score = signals[0]
            expected_assessment = (
                "agreed"
                if len(signals) == 2
                else "title_only"
                if diagnostic["title_score"] is not None
                else "body_only"
            )
            expected_relationship = (
                "text_understates"
                if expected_score < score
                else "text_overstates"
                if expected_score > score
                else "aligned"
            )
        require(
            (diagnostic["text_score"], diagnostic["assessment"], diagnostic["relationship"])
            == (expected_score, expected_assessment, expected_relationship),
            f"{case_id}: conflicting or missing text was incorrectly resolved",
        )
        expected_delta = None if expected_score is None else expected_score - score
        require(
            diagnostic["score_delta"] == expected_delta
            and (diagnostic["score_delta"] is None or type(diagnostic["score_delta"]) is int),
            f"{case_id}: incorrect text/diff score delta",
        )
        confounder = any(value != score for value in signals) if signals else None
        require(
            diagnostic["potential_confounder"] is confounder,
            f"{case_id}: incorrect potential-confounder flag",
        )
        require(
            diagnostic["title_explanation"].strip()
            and diagnostic["body_explanation"].strip()
            and diagnostic["sources"]
            == ["evidence.json#/title", "evidence.json#/body", "evidence.json#/files"],
            f"{case_id}: missing diagnostic explanations or evidence references",
        )
        require(
            case["text_triviality_score"] == expected_score
            and case["text_diff_triviality_relationship"] == expected_relationship
            and case["potential_triviality_confounder"] is confounder,
            f"{case_id}: stale diagnostic index",
        )
        text_display = str(expected_score) if expected_score is not None else "unknown"
        require(
            f"| {text_display} / {expected_relationship} |" in row,
            f"{case_id}: stale Markdown diagnostic",
        )
        text_relationship_counts[expected_relationship] += 1
        text_confounder_count += confounder is True
        for key, criterion in labels["criteria"].items():
            if criterion.get("type") != "ordinal":
                require(
                    criterion["status"] in {"passed", "failed", "not_assessable", "not_applicable"},
                    f"{case_id}: invalid status for {key}",
                )
            require(
                bool(criterion["explanation"].strip())
                and (
                    bool(criterion["sources"])
                    or (key == "review-coverage" and criterion["status"] == "not_assessable")
                ),
                f"{case_id}: missing explanation or required evidence references for {key}",
            )
            for reference in criterion["sources"]:
                if reference == "review.md":
                    require(has_review, f"{case_id}: review source without a review")
                    continue
                filename, pointer = reference.split("#", 1)
                require(
                    filename in {"evidence.json", "metadata.json"} and pointer.startswith("/"),
                    f"{case_id}: invalid source {reference}",
                )
                value = data[filename.removesuffix(".json")]
                for token in pointer[1:].split("/"):
                    token = token.replace("~1", "/").replace("~0", "~")
                    value = value[int(token)] if isinstance(value, list) else value[token]
        # Review coverage describes the supplied review, not the PR text.
        non_text = set(index["rubric_label_mirrors"]) | {"review-coverage"}
        failed = sorted(
            key
            for key, criterion in labels["criteria"].items()
            if criterion.get("status") == "failed" and key not in non_text
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
                    for key, item in labels["criteria"].items()
                    if item.get("type") != "ordinal" and key != "review-coverage"
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
        if labels["scenario"] == "boundary":
            require(
                BOUNDARY_SCORES.get(labels["family_id"]) == score,
                f"{case_id}: boundary case is not at its matched reference level",
            )
            require(
                evidence.title.startswith("chore(")
                and not BOUNDARY_LEAKS.search(evidence.title + "\n" + evidence.body),
                f"{case_id}: boundary text characterizes impact or names a level",
            )
        if labels["scenario"] == "omitted_change":
            require(
                len(evidence.files) == 2 and evidence.files[1].path not in evidence.body,
                f"{case_id}: omitted change is still mentioned in the description",
            )
        scenarios[labels["scenario"]] += 1
        qualities[labels["expected_quality"]] += 1
        triviality_counts[str(score)] += 1
        complexity_counts[labels["complexity"]] += 1
        if base:
            base_scenarios[labels["scenario"]] += 1
            base_qualities[labels["expected_quality"]] += 1
            base_triviality[str(score)] += 1
            base_complexity[labels["complexity"]] += 1
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
        dict(scenarios) == index["scenario_counts"]
        and set(base_scenarios.values()) == {10}
        and len(base_scenarios) == 10
        and all(scenarios[key] == value for key, value in EXTENSION_SCENARIOS.items()),
        "Expected ten cases in each of ten base scenarios plus the extension scenarios",
    )
    require(
        dict(qualities) == index["quality_counts"]
        and dict(base_qualities) == {"good": 10, "needs_revision": 90},
        "Expected ten good PRs and ninety PRs needing revision among the base cases",
    )
    require(len(families) == 10 and set(families.values()) == {10}, "Unbalanced change families")
    require(len(family_scenarios) == BASE_CASES, "Duplicate family/scenario pairs")
    require(
        dict(triviality_counts) == index["triviality_counts"]
        and dict(base_triviality) == {"0": 20, "1": 10, "2": 30, "3": 20, "4": 20},
        "Incomplete triviality coverage or stale score counts",
    )
    require(
        dict(complexity_counts) == index["complexity_counts"]
        and dict(base_complexity) == {"trivial": 30, "non_trivial": 70},
        "Stale complexity counts",
    )
    require(
        dict(review_variants) == index["review_variant_counts"]
        and dict(review_coverages) == index["review_coverage_counts"]
        and set(review_variants) == set(REVIEW_STATUSES)
        and set(review_coverages) == {"passed", "failed", "not_assessable"},
        "Stale or incomplete review counts",
    )
    require(
        dict(text_relationship_counts) == index["text_diff_triviality_counts"],
        "Stale text/diff diagnostic counts",
    )
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
        "triviality_counts": dict(triviality_counts),
        "complexity_counts": dict(complexity_counts),
        "review_variant_counts": dict(review_variants),
        "review_coverage_counts": dict(review_coverages),
        "text_diff_triviality_counts": dict(text_relationship_counts),
        "potential_triviality_confounders": text_confounder_count,
        "failed_criterion_counts": dict(failures),
        "valid_syntax_gibberish": valid_syntax_gibberish,
        "splits": validate_splits(root, index),
        "independent_review": validate_independent_review(
            root,
            {
                **index,
                "cases": [
                    case
                    for case in index["cases"]
                    if int(case["case_id"].removeprefix("pr-")) <= LEGACY_CASE_COUNT
                ],
            },
            expected_rubric_sha256=review_registry["revisions"]["historical-125"]["rubric_sha256"],
        ),
        "authored_independent_review": validate_independent_review(
            root,
            {**index, "cases": [case for case in index["cases"] if case["split"] == "evaluation"]},
            "independent-review/authored-reviews.json",
            expected_rubric_sha256=review_registry["revisions"]["review-2026-10-17"][
                "rubric_sha256"
            ],
        ),
        "legacy_independent_review": validate_independent_review(
            root,
            {
                **index,
                "cases": [
                    case
                    for case in index["cases"]
                    if int(case["case_id"].removeprefix("pr-")) <= LEGACY_CASE_COUNT
                ],
            },
            "independent-review/legacy-reviews.json",
            expected_rubric_sha256=review_registry["revisions"]["review-2026-10-17"][
                "rubric_sha256"
            ],
        ),
        "development_independent_review": validate_independent_review(
            root,
            {**index, "cases": [case for case in index["cases"] if case["split"] == "development"]},
            ACTIVE_REVIEW_ARCHIVES["development"],
        ),
        "evaluation_independent_review": validate_independent_review(
            root,
            {**index, "cases": [case for case in index["cases"] if case["split"] == "evaluation"]},
            ACTIVE_REVIEW_ARCHIVES["evaluation"],
        ),
    }


if __name__ == "__main__":
    print(json.dumps(validate(Path(__file__).resolve().parent), indent=2))
