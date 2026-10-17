# Advisory router specification

## Purpose and scope

Provide an offline CLI and a provider-independent engine for advisory PR review
routing. Accept owner-supplied JSON evidence and optional TOML policy; produce
validated JSON reports. The default mode uses deterministic mocks and makes no
API calls. Explicit local experiments use separately configured model adapters.

Hosted Jev/TypeSafe decisions and review adapters, GitHub evidence
collection, PR comments, approval/merge operations, and a Docker Action are deferred.

## Evidence contract

All objects reject unknown fields and use strict types. Required evidence fields:

| Field | Meaning |
| --- | --- |
| `repository` | Nonempty repository identifier |
| `number` | Positive integer PR number |
| `base_sha`, `head_sha` | Nonempty supplied commit identifiers |
| `title` | Nonempty PR title |
| `files` | Changed-file list, with unique paths |

Optional `body` defaults to an empty string. `files_complete` defaults to true;
collectors must set it to false when pagination or exports omit files. Each file
requires `path`, `status` (`added`, `modified`, `removed`, or `renamed`), and
nonnegative integer `additions`/`deletions`. Optional `patch` defaults to null and
`patch_truncated` defaults to false.

Patches use unified diff hunks, optionally preceded by file headers. Validate
declared old/new hunk counts and aggregate added/deleted counts against the
metadata. Support new files, removed files, multiple hunks, context lines, and
the no-final-newline marker. Missing/binary patches, metadata-only changes,
truncation, malformed hunks, and count disagreements require human review.

Coverage means the supplied textual export passes these checks. It does not
verify repository contents or prove that a collector supplied every changed file.
No patch content is applied or executed.

## Policy

The TOML file contains top-level fields; partial configurations use defaults.

| Field | Default | Constraint |
| --- | ---: | --- |
| `skip_confidence` | 0.95 | Finite score in [0, 1] |
| `review_confidence` | 0.85 | Finite score in [0, 1] |
| `max_input_bytes` | 100000 | Positive integer |
| `max_files` | 100 | Positive integer |
| `max_findings` | 5 | Positive integer |
| `title_format` | `"any"` | `"any"` or `"conventional_commit"` |
| `required_body_sections` | `[]` | Unique, nonempty, trimmed, single-line section names |
| `rubric_minimum_score` | `70` | Finite score in [0, 100] |
| `rubric_criteria` | `[]` | List of unique, validated weighted criteria |
| `nonsemantic_checks` | `[]` | Unique deterministic regex or script checks |
| `check_timeout_seconds` | `10` | External-check timeout in seconds, from 1 through 60 |

When configured, `title_format = "conventional_commit"` requires a lowercase
type, an optional non-whitespace scope, an optional breaking-change marker, and
a colon followed by a nonempty description (for example,
`feat(router): validate PR descriptions`). `required_body_sections` names exact
level-two Markdown headings outside fenced code blocks, allowing Markdown's
0–3-space heading indentation. Each heading must have non-heading text beneath
it before the next level-one or level-two heading. For example,
`["Summary", "Testing"]` requires populated `## Summary` and `## Testing`
sections.

PR-text checks are disabled by default. They are deterministic format checks,
separate from patch coverage and provider review. A format failure is reported
under `pr_text` and hands the PR off to a human before any provider is called.
The checks do not verify that the description accurately describes the diff.

The optional rubric evaluates multiple title/body criteria independently.
Each criterion has a unique `criterion_id`, description, text field (`title` or
`body`), check (`non_empty`, `min_words`, `contains`, or `section_nonempty`),
positive weight, and blocker type (`hard` or `soft`). `min_words` uses
`minimum_words`; `contains` and `section_nonempty` use `value`. A minimum-word
criterion gets proportional credit up to 100; the other checks score 100 or 0.
Each criterion's `pass_score` defaults to 100 and it passes when its score is
greater than or equal to `pass_score`. The overall score is the weighted average
of criterion scores. Any failed hard criterion prevents an automated pass
regardless of the aggregate score. Soft criteria lower the aggregate score; a
score below `rubric_minimum_score` also requires human review. Individual
results and their deterministic explanations are included in `rubric`.

`nonsemantic_checks` adds deterministic policy gates without presenting them as
semantic review. A regex check has a unique `check_id`, `description`, `kind =
"regex"`, a `target` (`"title"`, `"body"`, `"patch"`, or `"file_paths"`), and a
Python regular-expression `pattern`. It passes when the pattern matches unless
`must_match = false` is configured. A script check has `kind = "script"` and a
nonempty TOML `command` array; the validated evidence JSON is sent to the
command's standard input, and exit status zero means pass. Regexes run in a worker process bounded by `check_timeout_seconds`. Commands are
started without a shell in an isolated process group and are bounded by the same
timeout; timeout terminates the command and its descendants.

Checks run after coverage and alongside the existing PR-text and rubric gates,
before either provider is called. Every result is included under
`nonsemantic_checks`; a failed, timed-out, or unstartable check requires the
configured unresolved outcome. Check output is not included in reports, which
avoids leaking script output or PR content.

The input budget measures the UTF-8 bytes of the validated evidence's compact
JSON representation, including metadata and patches. A budget violation prevents
provider invocation; evidence is never silently truncated.

Threshold comparisons are inclusive. An unavailable confidence value is always
zero and cannot authorize skipping or clearing review, even at a zero threshold.

## Optional direct and feedback routing

All options are independent and compose with coverage, PR-text and rubric gates.
See [runnable direct examples](../examples/direct/README.md).

| Policy field | Default | Values / meaning |
| --- | --- | --- |
| `allow_direct_acceptance` | false | Enable `accept` decision recommendations |
| `allow_direct_rejection` | false | Enable `reject` decision recommendations |
| `acceptance_confidence` | 0.95 | Inclusive finite score in [0, 1] |
| `rejection_confidence` | 0.95 | Inclusive finite score in [0, 1] |
| `review_behavior` | `"escalate"` | `"escalate"` or `"feedback"` |
| `feedback_depth` | `"standard"` | `"standard"` or `"deep"` |
| `acceptance_feedback` | false | Collect non-blocking review after direct acceptance |
| `unresolved_outcome` | `"needs_human_review"` | Human handoff or `"rejected"` with route `"blocked"` |

Enabled confident direct rejection stops before review. Direct acceptance stops
unless acceptance feedback is enabled. Disabled/low-confidence direct decisions
continue to review; unavailable confidence never permits direct decisions.
Feedback runs once at the configured depth, retains findings, and completes
regardless of reviewer outcome/confidence without escalation. It yields `feedback`
or preserves `accepted` after acceptance; feedback alone does not accept a PR.
Unresolved policy applies to failed gates, incomplete evidence, provider errors,
explicit human requests, and unresolved escalating review. A failed feedback
provider also follows unresolved policy. No failure can yield acceptance.
Existing skip recommendations and thresholds still apply before review.
All routes remain advisory and CLI successful report generation still exits 0;
consumers must enforce rejected outcomes as blocking.

## Provider roles, contracts, and confidence

Use **decision provider** and **review provider** as the names for the two
provider roles (rather than using “decider” and “evaluator” interchangeably).
They describe responsibilities, not necessarily separate underlying models:

| Term | Responsibility | Output |
| --- | --- | --- |
| Decision provider (`DecisionProvider`) | Recommends whether to skip review, begin review, or hand off to a human. It chooses a route; it does not assess whether the diff is correct. | `Decision`: recommendation, confidence, reason, and provider identity |
| Review provider (`ReviewProvider`) | Assesses the diff at the requested review depth. | `ReviewResult`: outcome, confidence, summary, provider identity, and findings |

The engine/router validates evidence, applies policy checks and thresholds,
sequences provider calls, and escalates unresolved results; it is not a model.
Optional PR-text checks and rubric scoring are deterministic policy gates that
run before providers, not additional provider roles. A human reviewer handles
cases the automated route cannot resolve.

`standard` and `deep` are depth values passed to the review provider interface.
They are not two separate provider roles or necessarily different models. The
MVP uses one mock decision provider and one mock review provider; its `deep`
mock demonstrates the same interface and is not a stronger model.

Decision providers return a recommendation (`skip_review`, `review`,
`needs_human_review`, `accept`, or `reject`), confidence, reason, and provider identity. Review providers
accept a `standard` or `deep` depth and return an outcome (`no_concerns`,
`concerns`, or `uncertain`), confidence, summary, identity, and findings.

Confidence records its score and provenance: `mock`, `self_reported`,
`calibrated`, or `unavailable`. Calibrated scores require a calibration identifier.
The router uses scores according to policy; it does not calibrate them or assume
self-reported scores are probabilities.

Provider output is validated against these contracts at a single boundary
(`request_decision`/`request_review`). Providers may return models, dicts, or raw
JSON text; strict typing, enum values, confidence bounds, and required fields are
enforced and unknown fields are rejected. On a schema failure the call is retried
once (with `strict_schema=True` if the provider accepts that keyword). If the retry
is also invalid, the router requires human review and records only field locations
and error types, never provider output or exception text.

Findings include a changed-file path, optional positive new-side line, severity
(`low`, `medium`, `high`), title, and detail. Findings require a `concerns`
outcome. Unknown file references and invalid provider responses fail closed.
Line locations are supplied by providers; the engine does not validate their
semantic correctness.

## Routing and reporting

With the default policy, incomplete coverage goes directly to human review without provider calls.
A decision-provider recommendation of `needs_human_review` stops the pipeline.
A `skip_review` recommendation at or above the skip threshold skips review.

Other recommendations start review. Decision scores below the review threshold
or with unavailable provenance start at deep depth; otherwise review starts at
standard depth. A `no_concerns` result at or above the review threshold
completes the advisory review if no earlier review reported concerns. Otherwise
standard review escalates to deep review, and unresolved deep review requires
human review. Provider exceptions and malformed responses require human review;
exception text is excluded from reports.

Report schema version `"4"` includes:

- `advisory_only: true`, repository, PR number, base/head identifiers.
- `outcome`: `skipped`, `reviewed`, `needs_human_review`, `accepted`, `rejected`, or `feedback`.
- `route`: `no_review`, `standard`, `deep`, `human`, `direct`, `feedback`, or `blocked`.
- Reasons, coverage, optional decision, and executed review stages.
- `pr_text`, with whether policy checks are enabled, whether they pass, and any
  format issues.
- `nonsemantic_checks`, with whether checks are enabled and pass, plus each
  check's identifier, description, kind, result, and detail.
- `rubric`, with an optional weighted score, criterion scores/explanations,
  failed hard blockers, failed soft criteria, and whether the rubric passes.
- Deduplicated findings capped by policy and a unique `findings_omitted` count.

Version 3 added direct decisions and feedback outcomes/routes. Version 4 adds
the required `nonsemantic_checks` field; its contract rejects older schema
versions. Consumers of stored reports must retain an older parser or migrate
those reports before validation.

Stage finding lists are also capped individually. Escalation examines the complete
provider results before capping. Findings from earlier stages cannot be silently
discarded by a later clear response.

## Local model experiments and replay

`review --providers-config PATH` selects providers separately from policy TOML
and its environment overrides. Without this option the default mock behavior
remains. Each role independently selects `mock`, or `ollaya` for decisions and
`ollama` for review. Local adapters require an explicit installed model and a
loopback HTTP(S) origin without embedded credentials. Authenticated, hosted,
and cloud/remote models remain deferred. The router does not manage models.
The endpoint/backend selection and model name are separate settings. Optional
`instructions` supplement the selected model's prompt; optional
`backend_instructions` handle backend-specific cases. Supplemental instructions
cannot replace adapter-owned evidence-safety, output-contract, uncertainty, or
advisory-only constraints. The Ollaya decision backend also accepts the
`max_choice_options` setting (default 3, range 2–100) as an upper bound on options
in any choice question, including semantic rubric questions.

Ollaya uses `POST /v1/systemone` with complete evidence and a choice among
`skip_review`, `review`, and `needs_human_review`. Context overflow or explicit
truncation fails closed. Reasons deterministically describe the selected label.
Native confidence/probabilities are retained separately; routing confidence is
`unavailable` until identifiable task calibration exists. Upstream model
calibration alone does not establish PR-routing calibration.

Ollama uses `POST /v1/chat/completions`, separate instructions and untrusted
evidence messages, JSON-schema output, and configurable generation settings.
Model content contains outcome, numeric confidence, summary, and findings.
Adapter code supplies identity and `self_reported` confidence provenance.
Existing response and changed-file validation applies. Refusal, incomplete
generation, a different answering model, or invalid output fails closed.
Standard/deep use distinct prompts and output budgets with the same model;
they do not constitute independent corroboration.

Before review generation `/api/show` must report explicit `num_ctx` matching
provider `context_tokens`. UTF-8 request/schema/template bytes, framing headroom,
and reserved output tokens must fit configured and architectural context
limits. This conservative check assumes byte-fallback tokenization and ordinary
templates; custom tokenizer/template adoption requires verification. It may
reject inputs that would fit after tokenization. Evidence is never silently
summarized or truncated. Model version/digest inspection happens lazily after
engine preflight, so failed gates prevent all HTTP calls.

HTTP requests have a total deadline and bounded bodies, ignore system proxies,
and do not redirect or automatically retry. The existing boundary catches schema
errors from adapter normalization as well as returned payloads and retries once
with `strict_schema=True`. Raw provider errors never enter advisory reports.

Experiments require `shadow_skips = true` and reject policies enabling direct
acceptance or rejection. A wrapper retains original validated decisions and
turns skip recommendations into review requests with an explicit reason, outside
the engine. Report schema remains version 4. Separate artifacts contain original
and native decisions, uncapped reviews, model metadata, generation settings,
prompt/schema revision, usage, timing, and schema/transport failure counts.

`review` supports `--record PATH`, `--replay PATH`, and `--experiment-output PATH`
only with provider configuration. Artifact output defaults to
`reports/experiment-PR_NUMBER-HEAD_HASH.json`. Explicit recording writes a
version-1 tape containing origin, experiment fingerprint, and ordered HTTP
request/response or failure exchanges, including retry sequences. Private
runtime artifacts belong under ignored `reports/`; inspect and sanitize tapes
before committing them. Recording and replay are mutually exclusive.

Replay opens no HTTP client. Fingerprints cover provider/model settings, policy,
evidence, actual prompt contents/versions, and schemas. Every request and its
position must match. Header/request mismatches and missing/unused exchanges
produce status 2, even if the engine caught a replay exception and escalated.
There is no live fallback or implicit refresh.

`evaluate --corpus PATH --providers-config PATH [--config PATH]` runs validated
cases and writes per-case reports/artifacts and `summary.json` beneath
`--output-dir` (default `reports/evaluation`). `--record` writes case tapes;
`--replay-dir PATH` replays them. Corpus version 1 requires unique safe case IDs,
relative evidence paths beneath the corpus directory, categories, reference
routes, and expected concern anchors. Label status distinguishes provisional
from human-reviewed labels. Initial examples/tapes are explicitly provisional
and synthetic and do not claim model reviews.

Metrics count unsafe original skips, missed planted defects, findings unmatched
by reference anchors, human handoffs, schema/transport failures, and request
latency. Path/optional-line matching of uncapped native findings is a proxy
requiring human assessment. Replay uses recorded timing and does not assess
current model quality or speed. Preflight handoffs have no provider decision or
HTTP calls. Outputs are individually atomic/private; earlier case files can
remain after a later failure, and summary is written only after completion.
See [configuration and runnable commands](../examples/local-models/README.md).

## Formal and semantic assessment rubric examples

An evaluation corpus can optionally name `assessment_rubric`, a relative JSON
or TOML path, and each case can supply a `review` path to UTF-8 review text. These inputs
must stay beneath the corpus directory and cannot be overwritten by outputs.
Rubric reference labels require a status for every configured criterion and an
expected recommendation. Existing corpora without these fields retain their
behavior and recording fingerprints. The loader selects JSON for a `.json`
suffix (case insensitive) and otherwise retains TOML parsing for existing paths.
Both formats use identical Pydantic validation. Fingerprints use the validated
rubric contents, so changing only the serialization format does not invalidate
HTTP recordings. This applies to assessment rubrics, including explicit
`--assessment-rubric` overrides; policy and provider configuration retain TOML.
The rubric document includes routing actions and thresholds in addition to the
semantic questions sent to the decision provider.

This experiment rubric is distinct from deterministic `Policy.rubric_criteria`.
It configures unique criterion IDs, descriptions, improvement messages, and checks
(`conventional_title`, `body_section`, or `semantic`). Without `enforcement_rules`,
each criterion also sets a failure `action` (`block` or `suggest`). With
`enforcement_rules`, criteria must not set `action`; the rules define enforcement.
Formal checks reuse existing title/section
validators. Without `enforcement_rules`, a formal blocker prevents all HTTP calls
and leaves semantic criteria explicitly unassessed. With enforcement rules, formal
results are evaluated like other criteria, because the enforced set may depend on
semantic condition criteria. Semantic criteria carry instructions and a
description/review target and use the configured Ollaya decision adapter. Every
semantic question includes its configured description and criterion-specific
instructions, regardless of question type. The entire PR evidence and separately
supplied review are untrusted state data;
criterion instructions are separate. Document/review coverage is not proof of
code correctness, and review-provider responsibilities remain unchanged.

Semantic criteria are batched through `/v1/systemone`. Each criterion selects
`question_type`: `choice` (default), `noul`, `predicate`, or `score`. `predicate`
is an alias for `noul`; Ollaya requests use its native `noul` type. By default,
choices return `passed`, `failed`, or `uncertain`. A semantic choice criterion may instead
configure a `choices` list, with two or more entries. Each entry has a unique
lowercase `label`, provider-visible `description`, and optional canonical
`status` of `passed`, `failed`, or `uncertain`. The selected decision provider's
`max_choice_options` capability limits the number of entries (default `3`, from
2 through 100). The provider returns the custom label and its distribution. A
mapped label receives its configured status. An unmapped label remains available
to `routing_rules`; without a matching rule it maps conservatively to
`uncertain`.

Each ordered `routing_rules` entry names a semantic choice
`target_criterion_id`, a resulting stable `status`, and a nonempty `when`
mapping. Every `when` key is a semantic choice criterion and its nonempty label
list is matched against that criterion's native answer. Conditions are ANDed
across criteria and ORed within a criterion's label list. For each target, the
first matching rule applies; its resulting status then uses the target criterion's
configured `block` or `suggest` action, or its enforcement membership when
`enforcement_rules` exist. Rules can therefore combine
multiple native labels without bypassing conservative routing. Numeric criteria require explicit finite
`fail_threshold` and `pass_threshold`, with `0 <= fail < pass` within the native
range. Values at or below fail map to failed, values at or above pass map to
passed, and intermediate values require human assessment. Noul ranges from 0
to 1; score requires 2–10 distinct ordered `levels` and ranges from 0 to the
last level index. Higher values must mean greater criterion satisfaction.
Formal checks cannot use numeric types. Choices cannot configure numeric
thresholds or levels, numeric criteria cannot configure choices, and probability
criteria cannot configure score levels.

Optional ordered `enforcement_rules` select which criteria can affect the
recommendation. Each rule has a nonempty `when` mapping over semantic choice
criteria, matched exactly as routing conditions are, and at least one of two lists:

- `enforce` lists blocking requirements. A failed member produces a blocker and
  the recommendation `block`.
- `soft_enforce` lists non-blocking requirements. A failed member produces a
  suggestion, which yields `suggest_changes` unless the result is `block` or
  `needs_human_review`.

A list member is a criterion ID or an `any_of` group of two through sixteen
distinct criterion IDs, usable in either list. For each requirement, a passing
member satisfies it. An uncertain member, with no passing member, makes the
recommendation `needs_human_review` unless a blocker already produces `block`.
Criterion IDs must be distinct across both lists of a rule, and a rule must set at
least one non-empty list. The first matching rule applies. Criteria outside the
matched rule keep their native status in artifacts, with an explanation that they
do not affect routing. Rubrics with `enforcement_rules` must not set `action` on any
criterion, because enforcement membership is defined only by the rules. Rubrics
without `enforcement_rules` must set `action` on every criterion, where `block`
enforces the criterion as blocking and `suggest` as non-blocking. If no rule matches,
the decision fails closed: every criterion that is not a `when` condition is enforced
as blocking, so any failure produces `block`, and the result cannot be
`skip_and_approve`. Enforcement rules cannot reference deterministic or score criteria
in `when`.

Native response validation requires exactly the asked criterion IDs and types,
and exact choice labels or configured score legends/probability labels. Score
values outside the configured scale are invalid. Numeric thresholds are
experimental policy bands, not task calibration. A middle score may mean
partial satisfaction; it is mapped to human assessment without claiming model
uncertainty. Noul is a native probability of the configured proposition, not a
validated probability of a correct PR decision. Native typed answers are retained
per criterion in artifacts and evaluation summaries, alongside mapped statuses.
`--assessment-rubric` evaluates an alternative rubric against the same corpus
evidence, supplied reviews, and reference labels; its path is protected from
output replacement. The example comparison runner evaluates choice, noul, and
score variants with identical labels, using offline replay by default and local
inference only with `--live`. Rubric-specific request latency and schema-failure
counts exclude follow-up reviewer calls; total pipeline metrics still include
them. Replay durations are recorded HTTP timings, not current model latency.
Native scores are retained but task confidence remains unavailable. Missing
review text makes review-target criteria unavailable;
context truncation or unrecovered provider/schema errors cannot yield approval.
The complete request, including supplied review and rubric instructions, must
fit the policy input budget before any model/metadata request. Existing engine
preflight still prevents every provider call for incomplete evidence.

Known failed enforced blocking requirements yield `block`; otherwise uncertainty
in an enforced requirement yields `needs_human_review`; otherwise failed
soft-enforced requirements yield `suggest_changes`; `skip_and_approve` requires
all enforced requirements to pass. Results include criterion statuses/sources,
blockers, configured suggestions, and `skip_recommended` / `approval_recommended`.
Advice is always `advisory_only` with `automation_authorized: false`. There is no
aggregate threshold that can cancel a blocking failure, and task confidence is
never invented.

The rubric decision provider requests human review for blockers/uncertainty,
normal review for suggestions, and skip for all-pass advice. Existing shadow
mode records the original skip and continues reviewing. Report schema version 4
is unchanged; rubric advice is saved under `assessment_rubric` in experiment
artifacts and summaries. It cannot override coverage or unresolved review and
never approves or changes a PR. Policy direct decisions remain disabled.

Rubric configuration, actual prompt/schema contents and revisions, and complete
supplied review text join the fingerprint only for rubric experiments. Native
normalization uses the existing single schema retry. Evaluation compares each
criterion status and recommendation with independent reference labels, counting
blockers, suggestions, skip/approval advice, mismatches, and unsafe approval
recommendations. Reference-label edits alone do not require fresh inference.
See the [nine-case, six-criterion examples](../examples/rubric-review/README.md).
Labels remain provisional and offline tapes explicitly synthetic.

The separate [synthetic PR benchmark](../examples/synthetic-prs/README.md) contains
149 sanitized fictional PRs across 30 families. Its fixed `splits.json` assigns
the original 125 cases (18 families) to development and 24 independently authored
cases (12 new families) to evaluation. Matching description/review variants of a
family stay together. Validation rejects overlapping families, identical patch
sets across partitions, stale partition corpora, and missing author provenance.
Independent authoring and blind AI label review do not confer human-reviewed
ground truth; contested judgments retain their dissent and provisional labels.

The original 125 development cases also receive two fresh blind judgments per
criterion under the same current seven-criterion rubric and adjudication rules
as the 24 evaluation cases. `independent-review/legacy-reviews.json` records
these current judgments; `reviews.json` remains the unchanged historical
archive. Both raw reviewer assessments, source pointers, input hashes, prior
adjudications, and any current dissent are retained. A clear factual contradiction
can overturn author intent; unresolved interpretations receive `uncertain`.
Patch-based triviality adjudications must be consistent across identical family
diffs, independently of PR-text variants. The offline review reporter accepts
`--split development`, `--split evaluation` (default), or `--split all`, and
includes final adjudications and unresolved disagreements separately from
confusion against provisional author references. The benchmark's contested-case
view uses the latest review for each partition. No original PR input, author
annotation, rubric, or partition assignment is rewritten by the re-review.

The benchmark runners default to evaluation; `--split development` and
`--split all` are explicit alternatives. Scoring requires exactly one result for
each selected case and compares embedded references with pinned local labels.
Reports include support and expected-to-actual confusion per criterion,
scenario, reference triviality level, family, split, author, and scenario-by-level
intersection. Native ordinal triviality confusion is separate from its mapped
criterion status; all classified native labels map to `passed`. Missing answers
and explicit uncertainty stay separate and remain in accuracy denominators.
Reports count false passes, missed blocking recommendations, non-trivial changes
classified as trivial, and unsafe approval recommendations in every stratum.
The standalone triviality scorer also accepts `null` as an explicit abstention;
missing cases are errors. Input fingerprints include the split definition and
selected evidence, commit metadata, and supplied review contents. Scoring existing
summaries or predictions is offline and does not run inference. Agreement with
copied reference labels tests accounting only, and public family holdouts do not
establish external validity or authorize automated acceptance.

## Mock behavior

Added-line `MOCK_DECISION_ACCEPT` / `MOCK_DECISION_REJECT` markers return synthetic
direct decisions with mock confidence 0.99. Rejection wins if both occur. Policy
must enable direct decisions to honor them; otherwise routing continues to review.


The decision mock skips only modified prose documentation where every changed line
corrects a repeated adjacent word. Source files, additions/removals, changed
formatting syntax, and other documentation changes are reviewed. This deliberately
narrow rule is a demonstration and cannot establish semantic safety.

The review mock reports a synthetic medium-severity concern when an added line
contains `MOCK_REVIEW_CONCERN`. It clears recognized editorial corrections and
returns uncertainty for everything else. Both depths use the same deterministic
rules and clearly label confidence as mock-derived.

## CLI and acceptance

`pr-review-router review --input PATH [--config PATH] [--output PATH]` returns JSON
with mocks unless explicit provider configuration is supplied.
Help/version work without credentials. Successful advisory runs exit 0; invalid
input/policy or I/O exits 2. File writes are atomic, and validation failures leave
an existing report untouched. Evidence/policy cannot be overwritten by the report.

A fresh checkout installs from `uv.lock`, passes Ruff, formatting, codespell,
pytest, packaging, CLI smoke checks, and fresh-wheel execution without development
dependencies or model credentials. CI is read-only and uses verified action pins.
Fresh-wheel runtime dependencies are constrained by `uv.lock` for reproducible replay.
