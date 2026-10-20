# Synthetic PR quality corpus

157 fictional, sanitized pull requests across 30 change families with reusable
PR title/description quality annotations and actual change-triviality labels.
Each has metadata, a one- to three-commit list, complete unified file patches,
independent criterion annotations, and (for most) a supplied review. Supplied
reviews and their coverage assessments are legacy experiment dimensions; the
corpus and its communication annotations remain relevant to the intended scope.
Everything is checked in; loading, regeneration, and validation need no network
or credentials.

Start with the [human-readable index](INDEX.md) or [machine-readable manifest](index.json).

## Communication quality benchmark

All 157 cases carry reusable title/description annotations. The separate
`communication-v1` benchmark runs the new `assess` runtime and scores its final
communication verdict using canonical author labels under
`author-communication-v1`. The existing router benchmark, supplied reviews, and
review-reference archives remain unchanged. Reporter generation and consumer CI
enforcement are not implemented.

```sh
# Offline mocks: formal criteria run; unsupported semantics remain unresolved.
uv run python examples/synthetic-prs/benchmark_communication.py --split evaluation

# Explicit live decision inference after installing/configuring the selected model.
uv run python examples/synthetic-prs/benchmark_communication.py --split development --decider winnow --record --output-dir reports/communication-live

# Exact assessment-only replay, with no live fallback.
uv run python examples/synthetic-prs/benchmark_communication.py --split development --decider winnow --replay-dir reports/communication-live --output-dir reports/communication-replay
```

`communication-rubric.toml` selects these existing canonical annotation IDs:

| Assessment | Criterion IDs | Enforcement |
| --- | --- | --- |
| Title syntax and meaningfulness | `title_format`, `title_meaningful` | Required |
| Title agrees with patches | `title_diff_consistency` | Required |
| Substantive description | `description_present` | Required |
| Title and description agree | `description_title_consistency` | Required |
| Description covers every substantive patch change | `description_diff_consistency` | Required |
| Concrete motivation | `rationale` | Required |
| Relevant validation explanation | `testing` | Advisory |

The default split is the preserved 24-case evaluation partition. Development
contains 133 cases; `--split all` selects all 157. Family assignments and matched
variants stay intact. Model inputs contain only evidence and criterion questions:
no supplied `review.md`, annotations, scenario tags, expected outcomes, reference
summaries, or other oracle metadata. Reviewing labels and changing review text
cannot influence assessment fingerprints or communication decisions.

Outputs include per-case `*.assessment.json` and `*.provenance.json`, optional
`*.tape.json`, and `summary.json`. The summary records rubric, split, and reference
hashes; criterion confusion/agreement; final decision agreement; false acceptance;
unresolved assessments; family/scenario support; and each case's expected and
actual statuses. Criterion metrics compare against independently normalized
annotation statuses; final expected verdicts still come from the assessment engine.
Original reference statuses remain visible. `not_assessable`, `uncertain`, and
`not_applicable` are never converted into passing gates: without an explicit
applicability rule, they score as unresolved. Reports must be written outside the
preserved corpus.

Version 1 accepts communication-only rubrics with explicit `block`/`suggest`
actions and matching canonical annotation IDs; the runtime itself also supports
ordered enforcement and `any_of` rules. Alternate rubrics can be selected with
`--assessment-rubric`, and decider/profile settings with the usual catalog flags.
Rubric-ID mirrors are not independently counted. No annotations are rewritten.
Existing adjudications are not silently substituted for canonical criteria with
different definitions; these labels remain provisional until human validation.
Agreement with them does not establish task calibration or authorize automation.

Additional annotations remain available for future rubric versions, including
`changes_described`, `limitations`, `breaking_changes`, and `change_triviality`.
`description_commit_consistency` additionally requires companion commit metadata,
which the current evidence contract does not ingest. Extend annotations only
where required communication criteria lack matching labels, preserving provenance
and historical reference versions. Change scope can select communication
requirements without asserting code safety; testing explanations do not prove
that tests were actually executed.

## Layout and loading

Each `prs/pr-NNN/` directory contains:

| File | Contents |
| --- | --- |
| `evidence.json` | Repository, PR number, title, Markdown body, base/head identifiers, and changed files with status, line counts, and unified patches. Accepted directly by `PullRequestEvidence` and the `review` CLI. |
| `metadata.json` | Fictional author, reserved-domain URL, timestamps, branch names, aggregate diff statistics, and ordered `commits`. Each commit has a SHA-shaped identifier, parent, message, author, timestamp, changed paths, and line counts. |
| `annotations.json` | Family, scenario, tags, expected quality, review variant, fourteen canonical status-based labels, rubric-ID mirror labels, a graded change-triviality criterion, separate text/diff framing diagnostics, explanations and evidence references, and a truthful reference summary. |
| `review.md` | Optional supplied review text, passed to the router as separate review input and never part of `evidence.json`. Absent for the `none` review variant. |

The manifest and annotations use `schema_version: 3`, adding the rubric's
Summary-section check and a review-coverage label to the existing oracle criteria. `index.json` identifies rubric-ID mirrors of
existing oracle criteria, and case failure summaries count each canonical
criterion once. Metadata retains schema version 1, and CLI evidence is
unchanged. Consumers of version 2 should handle these additional criteria
before reading version 3.
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

Ten factual change families appear in each of ten primary scenario groups
(PRs 001–100), followed by matched extensions (PRs 101–125) and independently
authored held-out families (PRs 126–149):

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
| 101–110 | Rationale present but only restating the edits (what, not why) | 10 |
| 111–117 | Summary concretely describes only the first of two changed files | 7 |
| 118–125 | Neutral `chore` text around adjacent triviality boundaries (four matched pairs) | 8 |
| 126–137 | Independent author A: Unicode prose, accessible label, lint pin, CI permissions, SQL migration, Java retry boundary | 12 |
| 138–149 | Independent author B: resource cleanup, timezone guide, localization constant, reproducible archives, Ruby loader migration, optional schema field | 12 |
| 150-157 | Additional supplied-review variants for the eight boundary families | 8 |

PRs 150-157 retain their families' original patches but rotate the supplied-review
variants. Two blind AI reviewers assessed the new inputs for the
frozen `review-2026-10-18` snapshot; raw judgments, disagreements, and
adjudication explanations are retained. The later
`review-2026-10-18-rubric-refresh` snapshot is now `latest`. These labels remain
provisional, not human-reviewed. The original 149 case files and prior review
archives are unchanged.

There are 35 good cases and 122 cases needing revision, with 277 commits total.
The invariants for the first 100 cases (ten good, ninety needing revision, and the
20/10/30/20/20 triviality distribution) are unchanged; the extension adds eight
new families and reuses ten existing ones.
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
good description or a specific documentation defect. The set remains a small
controlled benchmark with repeated language and an artificial class balance.

### Independent authoring and family holdouts

Two separate authoring agents wrote PRs 126–149 without reading each other's
cases or the existing family templates/oracle labels. Their complete authored
sources and protocols are preserved in [`authored/author-a.json`](authored/author-a.json)
and [`authored/author-b.json`](authored/author-b.json). The packager only constructs
unified patches, commit accounting, source pointers, and indexes; it does not
apply the existing text or review templates to these cases. Each new family has
two variants sharing identical patches but independently written communication
and supplied-review contrasts. Authorship is separate from label-review provenance.

[`splits.json`](splits.json) fixes **development: 133 cases / 18 families** and
**evaluation: 24 cases / 12 new families**. `corpus-development.json` and
`corpus-evaluation.json` are directly accepted by `evaluate`. The router benchmark
defaults to a random 60-case sample across the full corpus, exactly two per family.
The standalone triviality CLI still defaults to evaluation. Explicit
`--split development`, `--split evaluation`, and `--split all` remain available.
Validation rejects family
overlap, identical patch sets across partitions, missing assignments, or stale
partition corpora. Every index entry records `split` and `author_id`; these are
evaluation metadata and must also be withheld from model inputs.

Freeze partitions before model runs and keep all matched variants together.
These are public family holdouts, not secret test data; do not tune on evaluation
results and then call them unseen. Both authors and reviewers use the same model
family, and the rubric remains shared. Independent authoring broadens language
and domains but does not establish human validity. Genuine boundary disputes
are retained explicitly rather than turned into high-confidence references.

### Supplied reviews

Every case outside the `none` variant has a `review.md`. The original 125 reviews describe their **actual**
patches, whatever the PR text claims. The variant rotates by family and scenario
so each family sees every variant. `review-coverage` labels the review, not the
PR text, so it never changes `expected_quality` or `failed_criteria`.

| Variant | Review content | `review-coverage` | Cases |
| --- | --- | --- | ---: |
| `none` | No review supplied | `not_assessable` | 25 |
| `complete` | Addresses every changed file and its implications | `passed` | 31 |
| `open_concern` | Complete, plus an open question that must be answered before merging | `passed` | 27 |
| `partial` | Addresses only some changed files | `failed` | 19 |
| `off_target` | Reviews a different change | `failed` | 7 |
| `generic` | Approves without a specific comment | `failed` | 23 |
| `ambiguous` | Refers only to unseen discussion and unspecified items ("the second change", "as discussed") | `not_assessable` | 25 |

An unresolved concern does not reduce coverage; coverage asks whether each
change was addressed, not whether the review is satisfied. The `ambiguous` and
`none` variants both map to `uncertain` for the router, since there is nothing to
tie to the patches. A generic approval is assessable but addresses no change,
so it fails. For an assessable review, check every changed file and its
implications; filenames need not appear when the change is clear. Because every enforcement rule enforces or soft-enforces
`review-coverage`, the oracle-derived recommendations include
`skip_and_approve` and `suggest_changes` as well as `block` and
`needs_human_review`.

### Contrastive PR-text cases

Two groups give the same patches as an existing good case a different, specific text defect:

- `what_not_why` (PRs 101–110): the Rationale section is present and concrete but
  only repeats the Summary, which fails regardless of the section heading.
  Compare with `missing_rationale` (no section) and the vague `additional_cases`
  variant ("makes things better"). For example, "The repeated word confuses
  readers" explains why; "Replace the repeated word" only says what changed.
- `omitted_change` (PRs 111–117): the Summary concretely describes only the first
  of two files and never mentions the second. Check all files within one
  `description-coverage` assessment; omitting a substantive change fails even
  when another file is covered (for example, describing a code change but not
  its added tests), and literal filenames are not required. Compare with
  `missing_change_description`, where the body gives motivation and generic
  Testing/Limitations text but makes no concrete edit claim, so coverage is
  `uncertain`. A description that makes concrete claims but omits a change
  **fails** `description-coverage`.

`rubric.toml` now states these rules in the criterion instructions: restating
edits fails `change-rationale`, an empty or non-specific description is
`uncertain` (not `passed`) for `description-coverage`, and a generic, partial,
or off-target review fails `review-coverage` while a missing or unresolvable one
is `uncertain`.

### Triviality boundary pairs

PRs 118–125 use eight new families, each with a neutral `chore(...)` title and
text that states facts without calling the change safe, breaking, mechanical, or
public. Adjacent levels share a file shape, so the diff alone decides:

| Pair | Lower level | Higher level | Distinguishing evidence in the patch |
| --- | --- | --- | --- |
| 0 / 1 | `comment-typo` (0): a comment word removed | `rename-local` (1): a local variable renamed everywhere in `total()` | Only the comment changes vs. executable lines change with the same behavior |
| 1 / 2 | `extract-constant` (1): literal `100` becomes `_PAGE_SIZE = 100` | `change-default` (2): `_PAGE_SIZE` goes from 100 to 50 | Added test expects 100 rows vs. 50 rows |
| 2 / 3 | `strip-input` (2): `clean()` strips whitespace | `escape-output` (3): `cell()` applies `html.escape` | Local normalization vs. a security-sensitive output boundary |
| 3 / 4 | `optional-param` (3): `timeout=30` added to `fetch` | `required-param` (4): `timeout` added with no default | Tests show `fetch('u')` still works vs. a call site must be updated to pass `30` |

`required-param` deliberately does not disclose the incompatibility, so
`breaking_changes` fails and its title and body understate level 4 (`text_understates`);
only the diff supports the reference level. `rubric.toml` also adds boundary rules
to the `change-triviality` instructions, and the `incompatible` level now covers an incompatible change
to a supported interface (such as a new required parameter), not only removal.

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
| `review-coverage` | The supplied `review.md` addresses every substantive change. `not_assessable` when no review is supplied or its references cannot be tied to the patches. Not part of `expected_quality`. |

### Regex-checkable vs semantic criteria

This benchmark intentionally mixes deterministic format checks and semantic
review judgments.

- Regex / programmatic checks: `title_format` is the only fully regex-oriented
  criterion in the core quality set. It can be validated with a Conventional
  Commits parser or a permissive regex, without reading the actual diff.
- Semantic / evidence-based checks: `title_meaningful`,
  `title_diff_consistency`, `description_present`, `rationale`,
  `changes_described`, `description_title_consistency`,
  `description_commit_consistency`, `description_diff_consistency`,
  `limitations`, `breaking_changes`, and `testing` all depend on meaning and
  cross-checking the title, body, commit messages, and actual patch. They are
  not reducible to a simple regex over the PR text.
- Diff-based ordinal assessment: `change_triviality` is also semantic in the
  sense that it is grounded in behavior, interfaces, and review scope; it is
  not inferred from the title or body text alone.

`description_present` is a borderline case: a regex can reject empty or
placeholder bodies, but the benchmark treats it as a semantic judgment because
it distinguishes meaningful prose from boilerplate or TODOs.

`passed` means the criterion is satisfied; `failed` means a known omission or
contradiction. `not_assessable` means there is no meaningful title or change
account to compare; it is not an observed contradiction. `not_applicable` is
used for breaking-change disclosure when the family removes no supported public
interface. Missing rationale can fail even for trivial edits. Missing content
is labeled separately from conflicting content, and `expected_quality` is
`needs_revision` when any quality criterion fails. Non-trivial changes do not
fail PR communication quality merely because of their triviality score.

## Independent review

### Historical review of the original cases

[`independent-review/reviews.json`](independent-review/reviews.json) holds an
independent AI review of the seven rubric criteria (`title-format`,
`summary-section`, `change-rationale`, `description-coverage`,
`review-coverage`, `testing-explanation`, `change-triviality`) for the original 125
cases: 875 labels, each with a rationale, evidence pointers, the oracle label,
both reviewers' labels, a `contested` flag, and the adjudicated label. Its
`label_status` is `independent_ai_review`. **This is not human review.** The
corpus, manifest, and router `corpus.json` stay `provisional`.

Protocol:

- Two reviewer passes (A and B) labeled every case from a packet containing only
  the title, description, commit messages, patches, supplied review, and the
  rubric text. Case IDs were opaque and shuffled differently for each pass; no
  annotation, scenario name, or README was available. Each label has a rationale
  of at most two sentences, evidence pointers (`title`, `body#Section`,
  `commit:N`, `patch:path`, `review`, `none`), and a `contested` flag for
  labels a reasonable reviewer could decide differently.
- The adjudicator compared both passes with the oracle and recorded every
  split. `resolution` is `unanimous`, `split_oracle_upheld`, or
  `oracle_overruled`; splits keep the dissenting rationale.
- Each case stores a hash of the evidence, commits, and review it was labeled
  against, and the file stores a hash of `rubric.toml`. `validate.py` fails if
  either changes, so edited cases or rubric text must be re-reviewed.

What the review found. A first pass over the earlier corpus left 29 of 875
labels non-unanimous, and the causes were fixable:

- 24 partial-coverage labels (descriptions and reviews that skip an added test
  file or documentation edit). Both reviewers followed the rubric as written,
  which said "behavior or configuration change", while the oracle definition
  counts tests and documentation. The rubric now says the latter.
- Three "missing change description" bodies (`pr-044`, `pr-045`, `pr-095`) still
  stated the behavior in their Breaking changes section. The section is now
  removed from those bodies.
- Two CSV cases (`pr-088`, `pr-115`) had a manual-testing line too vague to judge,
  which was then reworded without echoing the omitted tests.

All 125 cases were re-labeled blind on the corrected corpus and rubric, and the
three CSV cases again after a final wording change. Result: 874 of 875 labels
are unanimous with the oracle. The one split is `pr-087` `testing-explanation`
(Summary lists added tests but there is no Testing section; reviewers passed and
failed it), recorded as contested with the oracle upheld. 59 labels are
flagged contested by at least one reviewer, mostly `change-triviality`
(`cli-help` as mechanical versus bounded behavior, parameter additions as
substantive versus bounded) and `testing-explanation`; filter on
`contested: false` for high-confidence references.

Limitations:

- Both reviewers and the adjudicator are the same model family, and the
  adjudicator also wrote PRs 101–125 and the rubric wording. Agreement shows that
  the labels are consistent with the rubric as written and that ambiguities were
  surfaced; it does not establish external validity.
- The planted defects are deliberately clear, so high agreement is expected.
  The 13 other oracle criteria (`title_meaningful`, consistency labels, and so
  on) were not independently reviewed; `title-format` and `summary-section` are
  deterministic and agreed in all cases.
- Human review is still required before using these labels for calibration or
  quality assessment.

### Frozen review of the original 125 cases

[`independent-review/legacy-reviews.json`](independent-review/legacy-reviews.json)
preserves two blind passes over all 125 original cases under the rubric pinned
to the frozen `review-2026-10-17` revision. Reviewers received only opaque,
shuffled input packets and the rubric; they could not see author labels,
historical review judgments, case/scenario identities, or one another's answers.
This yields two independent judgments for each of 875 criterion labels, or 1,750
raw judgments. The original fixtures, author annotations, partition assignment,
and historical `reviews.json` remain unchanged.

See the [frozen development review findings](independent-review/LEGACY-REVIEW.md)
for the resulting disagreements and final judgments: `pr-087` now passes its
testing explanation, `pr-120/121` fail description coverage, and 35 cases in
three repeated diff families retain uncertain triviality adjudications. There
are 49 contested labels across 45 original cases. Reproduce the complete
scenario/criterion/reference-level confusion and adjudication findings offline:

```sh
uv run python examples/synthetic-prs/review_report.py --split development
uv run python examples/synthetic-prs/review_report.py --split all
```

These commands write `reports/synthetic-prs-label-review-development.json` and
`reports/synthetic-prs-label-review-all.json`. Report version 2 records archive
hashes by partition and an `adjudication_summary` including unresolved cases and
author-reference overrides. Confusion remains explicitly measured against the
provisional author labels; final adjudications are separately visible. The
label-review audit's `by_review_confidence` retains full confusion for contested
and uncontested cases; `review_confidence_support` counts their support. Benchmark `by_reference_confidence` follows the selected grading
revision; `original` is explicitly grouped as `author_intent`.
All participants use the same model family, and no human validity or live model
quality measurement is claimed.

### Held-out independent review

[`independent-review/authored-reviews.json`](independent-review/authored-reviews.json)
preserves two blind passes over all 24 held-out cases (168 labels per pass) for
the frozen `review-2026-10-17` and `review-2026-10-18` revisions.
Both saw only opaque input packets and the shared rubric, with no author labels,
scenario names, author identities, or other reviewer answers. The original
125-case archive is unchanged and retains its original scope. Input and rubric
hashes protect both archives against stale reviews.

The coordinator corrected one clear author-label error: `pr-137`'s validation
plan expected the old two-attempt limit after changing it to three. Both blind
reviewers failed `testing-explanation`; the original `passed` label and revision
reason remain recorded. Six triviality adjudications remain **uncertain**:
accessible-label spelling (`pr-128/129`), dependency release selection without
release effects (`pr-130/131`), and GNU-tar host support (`pr-144/145`). Their
provisional author scores remain distinct from the adjudications, and both
dissenting rationales are retained. There are 17 contested criterion labels;
agreement is not proof that the underlying judgments are settled.

See the [scenario review findings](independent-review/COVERAGE.md). To reproduce
full confusion by scenario, criterion, reference level, and their intersections
without inference:

```sh
uv run python examples/synthetic-prs/review_report.py
```

This writes `reports/synthetic-prs-label-review.json` for both reviewers, with
support counts, separate `review_confidence_support` counts, and full confusion
in `by_review_confidence`. Cases containing
any contested label are counted separately from `uncontested_ai` cases; neither
group claims human validation. This audit grades against original author intent
to expose disagreements, while benchmark scoring defaults to latest adjudications. Recommendation confusion in this report is a policy
projection of the archived labels, not an observed router run. No new live model
quality score is claimed.

### Current rubric-refresh review

[`review-2026-10-18-rubric-refresh`](independent-review/reference-revisions.json)
is the current `latest` snapshot for all 157 cases. Two fresh blind AI passes
received separately shuffled packets with opaque IDs and only the current rubric,
title, description, commits, patches, and supplied review. They did not see
annotations, scenarios, author identities, prior review judgments, or benchmark
outputs. A third isolated pass adjudicated reviewer splits, contested labels,
disagreements with prior references, and matched-family triviality consistency.
Raw reviewer rationales and evidence pointers are retained; older revision
archives remain unchanged and explicitly pinned to their original rubric hash.

These are AI assessments, not human-validated ground truth or calibration. Labels
remain provisional; disagreements and contested judgments remain visible.

## Change triviality scale and benchmark

Higher scores mean less trivial changes. Use the highest applicable level for
the actual diff; evaluate each matched family consistently even when its title,
body, or commit messages are misleading. A one-line security or public-interface
change can score higher than a larger prose edit. This is an ordinal assessment
of review scope and impact, not a calibrated safety or defect probability.

| Score | Level | Definition | Corpus examples | Cases |
| ---: | --- | --- | --- | ---: |
| 0 | `editorial` | Prose or link correction; no runtime/interface change | Guide typo, README link, comment typo, multilingual prose | 27 |
| 1 | `mechanical` | Local mechanical or presentation change in code preserving functional behavior and public interfaces | CLI help string, local rename, named constant, accessible label spelling | 17 |
| 2 | `bounded_behavior` | Local behavior/configuration change requiring edge-case review | Empty average, retry boundary, resource cleanup, dependency pin (contested) | 46 |
| 3 | `substantive` | New capability or security-sensitive behavior requiring broader review | CSV helper, authentication comparison, CI permissions, optional schema field | 30 |
| 4 | `incompatible` | Removal of, or incompatible change to, a supported interface/configuration requiring migration | Response-field rename, legacy config, required parameter, SQL column and Ruby loader removal | 29 |

Scores 0–1 are `is_trivial: true`, retaining the existing `complexity: "trivial"`
classification. Scores 2–4 are `is_trivial: false` and `complexity: "non_trivial"`.
The full corpus contains 44 trivial and 105 non-trivial changes. Both indexes
expose the score, level, and binary classification; the manifest defines the
scale and its counts. Every annotation includes a diff-based explanation.

### Patch-based reassessment

On 2026-10-15, GitHub Copilot inspected the original 100 complete file/patch sets and
found ten distinct sets, each repeated across ten PR-text variants. Assessing
the patches against the scale above corroborated the existing numerical scores;
they were already populated, not placeholders. The generated oracle explanations
now identify concrete changed files, behavior, and evidence limitations.

| Representative PR | Identical-Patch Variants | Score | Assessment |
| --- | --- | ---: | --- |
| `pr-001` | Every tenth PR through `pr-091` | 0 | Repeated word removed from documentation. |
| `pr-002` | Every tenth PR through `pr-092` | 1 | Displayed CLI help corrected; no parser change in the patch. |
| `pr-003` | Every tenth PR through `pr-093` | 0 | Documentation link corrected; destination existence unverified. |
| `pr-004` | Every tenth PR through `pr-094` | 2 | Empty-average behavior changes, with regression tests. |
| `pr-005` | Every tenth PR through `pr-095` | 2 | Lookup-key normalization changes behavior and input assumptions. |
| `pr-006` | Every tenth PR through `pr-096` | 2 | Accepted batch-size boundary increases from 50 to 100. |
| `pr-007` | Every tenth PR through `pr-097` | 3 | Authentication comparison primitive changes. |
| `pr-008` | Every tenth PR through `pr-098` | 3 | New CSV serialization capability and tests. |
| `pr-009` | Every tenth PR through `pr-099` | 4 | Documented response key removed and replaced without an alias. |
| `pr-010` | Every tenth PR through `pr-100` | 4 | Documented legacy configuration entrypoint deleted. |

These are AI reassessments of the supplied patches, not independent human labels
or test-execution results. Labels retain `provisional` status and
`synthetic_author_intent` origin; the reassessment does not replace their original
provenance. The scale measures change scope, not defect likelihood or authorization
to skip review. Compatibility judgments rely on the supplied API/setup documentation,
not verified downstream usage. Human review is still required before treating these
references as validated benchmark ground truth or calibration data.

After running your system on the selected partition, write one prediction per case in this form
(this excerpt shows only two of the required records):

```json
{
  "schema_version": 1,
  "predictions": [
    {"case_id": "pr-126", "score": 0},
    {"case_id": "pr-127", "score": 0}
  ]
}
```

```sh
uv run python examples/synthetic-prs/evaluate_triviality.py reports/triviality-predictions.json
```

The scorer requires exactly one integer 0–4 prediction (or `null` for explicit
uncertainty) for every selected case; duplicate,
unknown, missing, and out-of-range predictions are rejected. It reports exact
score accuracy, mean absolute error, binary trivial/non-trivial accuracy, a
reference-by-prediction confusion matrix with separate uncertainty counts, and
accuracy by PR communication quality. It also reports confusion and support
by scenario, family, author, split, reference level, and scenario-by-level.
Uncertain predictions stay in accuracy denominators for known reference levels;
mean absolute error uses only cases with both reference and prediction classified.
Latest review judgments are the default. Unknown reference levels remain explicit
and are excluded from numeric accuracy; native-label agreement and uncertainty
counts include them. Select `--reference-revision original` or a registered name
to grade against a different snapshot. Compare scores for this criterion and statuses for the fourteen
status-based criteria. This command scores supplied predictions offline; it performs no
inference and retains the provisional label status in its output. Withhold
reference triviality labels from model inputs and split by family as described
above. Perfect scores from copied reference labels only test the scorer.

## Text/diff triviality diagnostics

Every annotation has `diagnostics.text_diff_triviality` for future confounder
analysis. This is additive metadata outside `criteria`, with no pass/fail
status, weight, or effect on PR quality or benchmark scoring. The annotation
schema remains version 3; index split and author fields are additive metadata.

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
flag, and relationship counts. The 157 cases contain 139 aligned levels, nine
directional mismatches, eight conflicting text signals, and one unassessable
case. Seventeen cases have at least one text level differing from the diff.
Level agreement does not imply factual agreement: claiming a return value of
1 instead of 0 can remain level 2. Use the existing consistency criteria for
that distinction. Diagnostic flags identify candidate confounders without
establishing causation, and remain withheld from model inputs.

Testing sections describe fictional validation **plans**, never tests that this
project claims to have executed. They demonstrate relevant PR communication;
the corpus validator does not execute the code inside patches.

## Assessment rubric variants

`rubric_legacy.toml` preserves the original six-criterion rubric, where each
criterion's `action` determines whether failure blocks or suggests a change.
`rubric.toml` adds a choice-based `change-triviality` criterion and uses ordered
`enforcement_rules` to select blocking and soft criteria for editorial or
mechanical, bounded-behavior, and substantive or incompatible changes. The
scale follows the corpus's 0–4 triviality definitions; `uncertain` intentionally
matches no rule, so enforcement fails closed and cannot recommend approval.

Because the scale has six choices, the configured decision provider must support
at least six `max_choice_options`. Enforcement rules replace per-criterion
`action` fields; the legacy rubric retains those fields for comparison.
Classified scale labels map to `passed` and `uncertain` to `uncertain`; these
statuses only report whether a classification was made, because enforcement
rules match the effective label. The synthetic rubric maps native triviality
choices below `uncertain_below_confidence = 0.8` to `uncertain` for assessment
and enforcement. This is a conservative threshold on self-reported, uncalibrated
confidence, not a probability guarantee; the raw answer remains in the result.

## Router rubric benchmark

This existing benchmark retains supplied-review coverage as a legacy dimension.
It is separate from the [communication quality benchmark](#communication-quality-benchmark),
which reuses the corpus without making supplied review text a requirement.

`generate.py` also writes `corpus.json`, a router `evaluate` corpus for
`rubric.toml`. Its expected criterion statuses come from the oracle
(`not_assessable` becomes `uncertain`), and each expected recommendation is
computed by applying the rubric's own enforcement rules to those statuses and the
oracle triviality level. Those execution references remain preserved. The benchmark
recomputes grading references from **the latest adjudicated review by default**,
including revised criterion labels and unresolved triviality judgments. Explicit
[`policy.toml`](policy.toml) and [`providers.toml`](providers.toml) pin execution
to the local `winnow:e4b` Ollaya decision model with six choice options and the
deterministic mock reviewer. The benchmark measures rubric decisions; the mock
reviewer supplies synthetic fixtures and performs no substantive code review.

```sh
# Live local inference on a reproducible 60-case sample, recording HTTP exchanges.
uv run python examples/synthetic-prs/benchmark.py --seed 42 --record \
  --output-dir reports/synthetic-prs-benchmark

# Offline replay of that recording; no inference.
uv run python examples/synthetic-prs/benchmark.py --seed 42 \
  --replay-dir reports/synthetic-prs-benchmark \
  --output-dir reports/synthetic-prs-benchmark-replay

# Re-evaluate an explicit set of PRs with the current rubric; repeat --case-id.
uv run python examples/synthetic-prs/benchmark.py \
  --case-id pr-001 --case-id pr-101 --reference-revision original --record \
  --output-dir reports/synthetic-prs-rationale-rereview

# Rescore the same bound summary against the latest review, offline.
uv run python examples/synthetic-prs/benchmark.py \
  --summary reports/synthetic-prs-benchmark/summary.json \
  --output-dir reports/synthetic-prs-benchmark-rescored
```

Select grading labels independently of model inputs:

```sh
# List available revisions without inference, including the current rubric refresh.
uv run python examples/synthetic-prs/benchmark.py --list-reference-revisions

# Compare the same bound predictions with the canonical author-intent baseline.
uv run python examples/synthetic-prs/benchmark.py \
  --summary reports/synthetic-prs-benchmark/summary.json \
  --reference-revision original \
  --output-dir reports/synthetic-prs-original-grade

# Pin an immutable named review rather than the moving latest alias.
uv run python examples/synthetic-prs/benchmark.py \
  --summary reports/synthetic-prs-benchmark/summary.json \
  --reference-revision review-2026-10-18-rubric-refresh \
  --output-dir reports/synthetic-prs-pinned-grade
```

The frozen `review-2026-10-18` revision remains available for reproducing older
scores; it is no longer the `latest` alias.

The standalone `evaluate_triviality.py` accepts the same `--reference-revision`
selector and defaults to `latest`. `original` means the preserved canonical author
annotations, including recorded corrections, rather than reconstructed initial
drafts. `historical-125` covers development only: select `--split development`
with matching predictions. Unknown names and missing selected-case coverage are
errors; they never fall back to author labels. The registry
[`reference-revisions.json`](independent-review/reference-revisions.json) names
archive snapshots and their validation protocols. To add a revision, preserve old
archives, register new partition archives, and move `latest` when ready. Every
revision pins its rubric and case-input hashes. Frozen revisions remain usable
under their original rubric; `latest` must match the current rubric. Changing
inputs or rubric requires re-review and fresh predictions.

The runner removes `PR_REVIEW_ROUTER_*` environment overrides so the pinned
policy alone applies, then runs `pr-review-router evaluate` and writes
`benchmark.json` beside the router's per-case outputs. It records SHA-256
hashes of prediction inputs and separate grading sources; per-criterion
expected-to-actual counts; recommendation agreement and unsafe approvals; and
triviality accuracy from the native scale answers. Outputs stay under ignored
`reports/`.

The router benchmark defaults to `--split random --sample-size 60`. It randomly
selects in balanced family rounds without replacement: exactly two cases per
family. Use `--sample-size 30` for a quick run with exactly one per family.
Other sizes continue across families with remaining cases; samples smaller than
30 choose that many distinct families.
Without `--seed`, a fresh seed is generated; `benchmark.json` and
`benchmark-inputs.json` record `sampling.seed`, the method, size, and selected case
IDs. Repeat `--seed` and `--sample-size` to compare models on the same inputs.
Offline `--summary` rescoring automatically reuses the recorded selection and seed;
HTTP replay requires the original seed and size. The selected corpus, rubric, and
input snapshots are saved under the run directory's `sample-inputs/`.
These cross-family random samples are not family-disjoint held-out evaluations.
The standalone triviality scorer retains its evaluation default; explicit named
partitions and `--split all` remain available in both runners. Every metric
group includes its support. `by_scenario`, `by_triviality_level`, `by_family_id`,
`by_split`, `by_author_id`, and `by_scenario_and_triviality_level` each retain the
full criterion confusion. Thus a failed blocking criterion cannot disappear
inside overall agreement. Reports count false passes on failed/uncertain criteria,
missed blocks, unsafe approval recommendations, and non-trivial changes predicted
trivial. `uncertain` and `unanswered` are distinct prediction columns and stay in
denominators. Status agreement for `change-triviality` alone is insufficient:
all classified scale choices map to `passed`, so inspect the native ordinal matrix.
Unobserved intersections are omitted; present groups can have support one and
should not be interpreted as robust population estimates.

Results must contain each selected case exactly once. Version-2
`benchmark-inputs.json` captures selected evidence, commit metadata, review texts,
partitions, rubric, configuration, original run labels, and the completed summary
hash. Execution corpus paths, order, category, and rubric must match the fixed
index. The runner checks that inputs did not change during inference. Offline
`--summary` requires that binding and rejects changed inputs or results. It can
regrade unchanged predictions against new review snapshots without inference.
Version-1 bindings work when their original canonical labels remain verifiable;
unbound summaries require a fresh benchmark run.

`benchmark.json` version 4 records `grading_reference` (requested/resolved name,
protocol and source hashes) and `grading_references` (selected per-case judgments).
The raw `summary.json` retains execution references and its original accounting;
use `benchmark.json` for the chosen grade. No author annotations are rewritten.
The frozen `review-2026-10-18` grading retains 41 uncertain triviality references:
35 development and six evaluation cases. The rubric-refresh revision classifies
all 157 triviality references and retains contested criterion judgments.
Uncertain references in any revision have their own confusion row and stratum,
and cannot justify approval.
Numeric triviality accuracy uses only known reference levels, retaining
prediction abstentions in that denominator. An all-uncertain
stratum reports null numeric accuracy. `native_label_agreement` includes every
case, and counts of classifications or trivial predictions on unresolved
references expose potentially overconfident decisions. All labels remain
provisional AI judgments.

On 2026-10-15, a live run on the original 100 cases (no review text and the earlier
criterion instructions) had no schema or transport failures and no unsafe
approvals. Recommendation agreement was 0.86, with 13 oracle `block` cases
returned as `needs_human_review`. The deterministic title and Summary checks
agreed fully. `change-rationale` passed 18 oracle failures; `description-coverage`
passed 14 oracle `not_assessable` cases. Triviality was exact for 73 cases,
classified in 99, and binary trivial/non-trivial agreement was 0.95; errors were
mostly adjacent (editorial as mechanical, bounded as substantive), plus four
incompatible changes classified as mechanical. A second live run and a replay
both reproduced the benchmark exactly. That recording does not apply to the current
corpus or rubric instructions; rerun with `--record` before comparing results.

Review text is supplied for most cases, so the benchmark now exercises
`skip_and_approve` and `suggest_changes`. About a third of cases still carry no
review or an unresolvable one, which keeps `review-coverage` uncertain and the
recommendation at `needs_human_review` unless another criterion blocks. The
benchmark hash record includes a digest of all review texts. These labels remain
provisional and are not human-reviewed.

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
