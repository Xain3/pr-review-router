# Synthetic PR quality corpus

100 fictional, sanitized pull requests for benchmarking PR title and description
quality. Each has metadata, a one- or two-commit list, complete unified file
patches, and independent criterion annotations. Everything is checked in;
loading, regeneration, and validation need no network or credentials.

Start with the [human-readable index](INDEX.md) or [machine-readable manifest](index.json).

## Layout and loading

Each `prs/pr-NNN/` directory contains:

| File | Contents |
| --- | --- |
| `evidence.json` | Repository, PR number, title, Markdown body, base/head identifiers, and changed files with status, line counts, and unified patches. Accepted directly by `PullRequestEvidence` and the `review` CLI. |
| `metadata.json` | Fictional author, reserved-domain URL, timestamps, branch names, aggregate diff statistics, and ordered `commits`. Each commit has a SHA-shaped identifier, parent, message, author, timestamp, changed paths, and line counts. |
| `annotations.json` | Family, scenario, tags, expected quality, twelve criterion labels with explanations and JSON Pointer evidence references, and a truthful reference summary. |

The manifest uses `schema_version: 1` and relative paths to these three files.
Companion objects also use schema version 1. Identifiers are deterministic fake
values rather than real Git object hashes; no repositories need to be fetched.
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
    # Compare predicted criterion statuses with oracle["criteria"].
```

The manifest includes labels for filtering. Withhold its scenario, tags, quality,
and failed criteria, together with annotations and reference summaries, from the
system under evaluation. The router's current evidence schema does not ingest
commit lists. A harness assessing commit/description consistency must provide
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
reviewers. They assess supplied PR communication, not code correctness,
authorization to merge, or model calibration.

Each criterion has `status`, `explanation`, and `sources`. Sources such as
`evidence.json#/body` and `metadata.json#/commits` are file-relative JSON Pointers.

| Criterion | Interpretation |
| --- | --- |
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
`needs_revision` when any criterion fails.

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
