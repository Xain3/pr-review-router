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

Report schema version `"3"` includes:

- `advisory_only: true`, repository, PR number, base/head identifiers.
- `outcome`: `skipped`, `reviewed`, `needs_human_review`, `accepted`, `rejected`, or `feedback`.
- `route`: `no_review`, `standard`, `deep`, `human`, `direct`, `feedback`, or `blocked`.
- Reasons, coverage, optional decision, and executed review stages.
- `pr_text`, with whether policy checks are enabled, whether they pass, and any
  format issues.
- `rubric`, with an optional weighted score, criterion scores/explanations,
  failed hard blockers, failed soft criteria, and whether the rubric passes.
- Deduplicated findings capped by policy and a unique `findings_omitted` count.

Version 3 adds direct decisions and feedback outcomes/routes; its contract rejects
older schema versions. Consumers of stored reports must retain an older parser
or migrate those reports before validation.

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
the engine. Report schema remains version 3. Separate artifacts contain original
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
