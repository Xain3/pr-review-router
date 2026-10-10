# Synthetic PR quality corpus

100 fictional, sanitized pull requests for benchmarking PR title and description
quality and actual change triviality. Each has metadata, a one- or two-commit
list, complete unified file patches, and independent criterion annotations.
Everything is checked in; loading, regeneration, and validation need no network
or credentials.

Start with the [human-readable index](INDEX.md) or [machine-readable manifest](index.json).

## Layout and loading

Each `prs/pr-NNN/` directory contains:

| File | Contents |
| --- | --- |
| `evidence.json` | Repository, PR number, title, Markdown body, base/head identifiers, and changed files with status, line counts, and unified patches. Accepted directly by `PullRequestEvidence` and the `review` CLI. |
| `metadata.json` | Fictional author, reserved-domain URL, timestamps, branch names, aggregate diff statistics, and ordered `commits`. Each commit has a SHA-shaped identifier, parent, message, author, timestamp, changed paths, and line counts. |
| `annotations.json` | Family, scenario, tags, expected quality, twelve quality labels, a graded change-triviality criterion, separate text/diff framing diagnostics, explanations and evidence references, and a truthful reference summary. |

The manifest and annotations use `schema_version: 2`, adding the ordinal
triviality criterion alongside the twelve status-based quality criteria.
Metadata retains schema version 1, and CLI evidence is unchanged. Consumers of
version 1 should handle the new ordinal shape before reading version 2.
The manifest uses relative paths to these three files. Identifiers are
deterministic fake values rather than real Git object hashes; no repositories
need to be fetched.
Each commit changes exactly one file once, in listed order, so its changed path
identifies its complete patch in `evidence.json`. Commit parents form a linear
chain from `base_sha` to `head_sha`; commit statistics sum to PR statistics.

Load inputs separately from the benchmark oracle:

```python
import json
from pathlib import Path

root = Path("examples/synthetic-prs")
index = json.loads((root / "index.json").read_text())
for case in index["cases"]:
    evidence = json.loads((root / case["evidence"]).read_text())
    metadata = json.loads((root / case["metadata"]).read_text())
    model_input = {"pull_request": evidence, "commits": metadata["commits"]}
    # Send only model_input to the system being benchmarked.
    oracle = json.loads((root / case["annotations"]).read_text())
    # Compare predicted quality statuses with oracle["criteria"].
    triviality = oracle["criteria"]["change_triviality"]
    # Compare the model's integer triviality score with triviality["score"].
```

The manifest includes labels for filtering. Withhold its scenario, tags, quality,
failed criteria, triviality/complexity labels, and diagnostics, together with annotations and
reference summaries, from the system under evaluation. The router's current
evidence schema does not ingest commit lists. A harness assessing commit/description consistency must provide
the companion commits explicitly. This manifest is a PR-text benchmark index,
not the routing CLI's separate `evaluate --corpus` schema.

## Coverage

Ten factual change families appear in each of ten primary scenario groups:

| PRs | Primary scenario | Count |
| --- | --- | ---: |
| 001–010 | Good conventional titles, rationale, accurate summaries, validation plans, scope limitations, and compatibility disclosures | 10 |
| 011–020 | Meaningful titles outside Conventional Commits syntax | 10 |
| 021–030 | Gibberish titles, including five with formally valid syntax | 10 |
| 031–040 | Missing rationale: three trivial changes and seven non-trivial changes | 10 |
| 041–050 | Motivation present but concrete changes omitted | 10 |
| 051–060 | Title describes a different change from the body and diff | 10 |
| 061–070 | A commit message contradicts the body while its patch matches the body | 10 |
| 071–080 | Title and commit messages align with a false description that contradicts the diff | 10 |
| 081–090 | Empty, whitespace-only, placeholder, and heading-only bodies; vague rationale; omitted limits/testing; partial coverage; undocumented breaking changes | 10 |
| 091–100 | Combinations of title, rationale, summary, commit, diff, and compatibility failures | 10 |

There are 10 good cases and 90 cases needing revision, with 170 commits total.
Scenario groups identify the primary defect; individual criteria record all
known failures, including secondary omissions in a deliberately false body.
Families cover guide/help/link corrections, empty averages, cache normalization,
batch limits, token comparisons, CSV serialization, a public response-field
rename, and removal of a legacy configuration. Diffs include modified, added,
and removed files. The last two families remove supported interfaces and need
migration. Other families can change behavior; their reference descriptions
explain relevant implications without classifying every behavior change as a
public-interface removal.

These matched variants are deliberate: the same code change can appear with a
good description or a specific documentation defect. Split train/test data by
`family_id` to avoid putting the same diff in both partitions. The set is a small
controlled benchmark, with repeated language and an artificial class balance;
it can later be supplemented with sanitized real PRs using the same layout.

## Annotation rubric

All labels have `label_status: "provisional"` and
`label_origin: "synthetic_author_intent"`. They encode planted case expectations,
independently of provider outputs, and have not been labeled by external human
reviewers. They assess supplied PR communication and diff triviality, not code
correctness, authorization to merge, or model calibration.

Quality criteria have `status`, `explanation`, and `sources`. The ordinal
`change_triviality` criterion has `type: "ordinal"`, integer `score`, `level`,
boolean `is_trivial`, `explanation`, and `sources`; it has no pass/fail status.
Sources such as `evidence.json#/body` and `metadata.json#/commits` are file-relative
JSON Pointers.

| Criterion | Interpretation |
| --- | --- |
| `change_triviality` | The actual diff's 0–4 triviality score, grounded in behavior, interfaces, and review scope rather than the title, body, commit messages, or line count. |
| `title_format` | Conventional Commits syntax accepted by the router. A gibberish subject can pass syntax. |
| `title_meaningful` | The title conveys an intelligible change. |
| `title_diff_consistency` | A meaningful title agrees with the actual patches. |
| `description_present` | The body contains substantive prose; empty headings and TODO do not qualify. |
| `rationale` | A concrete reason for the actual changes is explained. Restating changes or generic improvement claims fails. |
| `changes_described` | Concrete change claims are present. Their accuracy is assessed separately. |
| `description_title_consistency` | The body and meaningful title agree. |
| `description_commit_consistency` | The body covers and agrees with every commit's stated changes. |
| `description_diff_consistency` | The body accurately covers substantive code, configuration, documentation, and test changes in all patches. |
| `limitations` | Relevant scope boundaries, risks, or unsupported cases are explained. |
| `breaking_changes` | An incompatible public-interface removal is disclosed with migration guidance. |
| `testing` | Relevant validation is described, or a specific reason it does not apply is given. |

`passed` means the criterion is satisfied; `failed` means a known omission or
contradiction. `not_assessable` means there is no meaningful title or change
account to compare; it is not an observed contradiction. `not_applicable` is
used for breaking-change disclosure when the family removes no supported public
interface. Missing rationale can fail even for trivial edits. Missing content
is labeled separately from conflicting content, and `expected_quality` is
`needs_revision` when any quality criterion fails. Non-trivial changes do not
fail PR communication quality merely because of their triviality score.

## Change triviality scale and benchmark

Higher scores mean less trivial changes. Use the highest applicable level for
the actual diff; evaluate each matched family consistently even when its title,
body, or commit messages are misleading. A one-line security or public-interface
change can score higher than a larger prose edit. This is an ordinal assessment
of review scope and impact, not a calibrated safety or defect probability.

| Score | Level | Definition | Corpus examples | Cases |
| ---: | --- | --- | --- | ---: |
| 0 | `editorial` | Prose or link correction; no runtime/interface change | Guide typo, README link | 20 |
| 1 | `mechanical` | Local mechanical or presentation change in code preserving functional behavior and public interfaces | CLI help string correction | 10 |
| 2 | `bounded_behavior` | Local behavior/configuration change requiring edge-case review | Empty average, key normalization, batch boundary | 30 |
| 3 | `substantive` | New capability or security-sensitive behavior requiring broader review | CSV helper, authentication comparison | 20 |
| 4 | `incompatible` | Supported interface/configuration removal requiring migration | Response-field rename, deleted legacy config | 20 |

Scores 0–1 are `is_trivial: true`, retaining the existing `complexity: "trivial"`
classification. Scores 2–4 are `is_trivial: false` and `complexity: "non_trivial"`.
The full corpus contains 30 trivial and 70 non-trivial changes. Both indexes
expose the score, level, and binary classification; the manifest defines the
scale and its counts. Every annotation includes a diff-based explanation.

After running your system on the inputs, write all 100 predictions in this form
(this excerpt shows only two of the required records):

```json
{
  "schema_version": 1,
  "predictions": [
    {"case_id": "pr-001", "score": 0},
    {"case_id": "pr-002", "score": 1}
  ]
}
```

```sh
uv run python examples/synthetic-prs/evaluate_triviality.py reports/triviality-predictions.json
```

The scorer requires exactly one integer 0–4 prediction for every case; duplicate,
unknown, missing, and out-of-range predictions are rejected. It reports exact
score accuracy, mean absolute error, binary trivial/non-trivial accuracy, a
reference-by-prediction confusion matrix, and accuracy by PR communication
quality. Compare scores for this criterion and statuses for the twelve quality
criteria. This command scores supplied predictions offline; it performs no
inference and retains the provisional label status in its output. Withhold
reference triviality labels from model inputs and split by family as described
above. Perfect scores from copied reference labels only test the scorer.

## Text/diff triviality diagnostics

Every annotation has `diagnostics.text_diff_triviality` for future confounder
analysis. This is additive metadata outside `criteria`, with no pass/fail
status, weight, or effect on PR quality or benchmark scoring. Schema version 2
and the thirteen existing criteria remain unchanged.

The diagnostic separately records the level implied by the **title** and the
**body**, using the same 0–4 scale. These are provisional author annotations of
the stated edits, not empirical claims about how a reader or model perceives
them. Commits and the actual diff are excluded when projecting the text levels;
the existing diff reference score is used only for comparison. Specific stated
edits take precedence over generic boilerplate: an explicit option removal
implies level 4 even if a footer claims all interfaces are preserved.

| Field | Meaning |
| --- | --- |
| `diff_score` | The existing `change_triviality` reference score, repeated for convenience. |
| `title_score`, `body_score` | Separately inferred levels; `null` if the corresponding text lacks an interpretable change claim. |
| `text_score` | Combined level when available signals agree, or the only available signal; `null` for conflicting or insufficient text. |
| `assessment` | `agreed`, `title_only`, `body_only`, `conflicting`, or `not_assessable`. |
| `relationship` | `aligned`, `text_understates`, `text_overstates`, `conflicting_text`, or `not_assessable`. |
| `score_delta` | Text score minus diff score; negative means understatement. Null when no combined score is available. |
| `potential_confounder` | True when at least one known text level differs from the diff, false when all available levels agree, or null when neither is assessable. |
| `title_explanation`, `body_explanation`, `sources` | Reasons for each text projection and references to title, body, and diff. |

For example, `pr-079` has a level-4 response-field removal while its title and
body claim level-1 formatting with unchanged fields: the diagnostic records
`text_understates` and a delta of -3. In `pr-072`, the text claims a level-4 CLI
option removal while the diff only corrects a level-1 help string. In `pr-051`,
title and body imply different levels, so both projections are retained and no
combined level is forced. `pr-092` has gibberish plus an empty body; its projected
levels stay null instead of being labeled trivial.

The index exposes the combined text score, relationship, potential-confounder
flag, and relationship counts. The 100 cases contain 88 aligned levels, three
directional mismatches, eight conflicting text signals, and one unassessable
case. Eleven cases have at least one text level differing from the diff.
Level agreement does not imply factual agreement: claiming a return value of
1 instead of 0 can remain level 2. Use the existing consistency criteria for
that distinction. Diagnostic flags identify candidate confounders without
establishing causation, and remain withheld from model inputs.

Testing sections describe fictional validation **plans**, never tests that this
project claims to have executed. They demonstrate relevant PR communication;
the corpus validator does not execute the code inside patches.

## Run and maintain

From the repository root after `uv sync --locked`:

```sh
# Validate all fixtures, cross-file references, commits, labels, and coverage.
uv run python examples/synthetic-prs/validate.py
uv run pytest tests/test_synthetic_pr_corpus.py

# Inspect a good input or an invalid conventional title with the existing policy.
uv run pr-review-router review --input examples/synthetic-prs/prs/pr-001/evidence.json --config examples/pr-text/policy.toml
uv run pr-review-router review --input examples/synthetic-prs/prs/pr-011/evidence.json --config examples/pr-text/policy.toml

# Recreate all data and indexes deterministically after editing the generator.
uv run python examples/synthetic-prs/generate.py
uv run python examples/synthetic-prs/validate.py
```

Mocks and deterministic format checks do not assess semantic description
quality or commit consistency. CLI output is advisory. Corpus validation checks
structural integrity and title syntax, while semantic annotations remain
explicit, inspectable reference expectations. Some fixture text and removed
patch lines contain intentional typos for the planted correction cases.
