# Mock-provider MVP specification

## Purpose and scope

Provide an offline CLI and a provider-independent engine for advisory PR review
routing. Accept owner-supplied JSON evidence and optional TOML policy; produce
validated JSON reports. The MVP uses deterministic mocks and makes no API calls.

Real Jev/Typesafe decisions, OpenAI-compatible review adapters, GitHub evidence
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

The input budget measures the UTF-8 bytes of the validated evidence's compact
JSON representation, including metadata and patches. A budget violation prevents
provider invocation; evidence is never silently truncated.

Threshold comparisons are inclusive. An unavailable confidence value is always
zero and cannot authorize skipping or clearing review, even at a zero threshold.

## Provider contracts and confidence

Decision providers return a recommendation (`skip_review`, `review`, or
`needs_human_review`), confidence, reason, and provider identity. Review providers
accept a `standard` or `deep` depth and return an outcome (`no_concerns`,
`concerns`, or `uncertain`), confidence, summary, identity, and findings.

Confidence records its score and provenance: `mock`, `self_reported`,
`calibrated`, or `unavailable`. Calibrated scores require a calibration identifier.
The router uses scores according to policy; it does not calibrate them or assume
self-reported scores are probabilities.

Findings include a changed-file path, optional positive new-side line, severity
(`low`, `medium`, `high`), title, and detail. Findings require a `concerns`
outcome. Unknown file references and invalid provider responses fail closed.
Line locations are supplied by providers; the engine does not validate their
semantic correctness.

## Routing and reporting

Incomplete coverage goes directly to human review without provider calls.
A decision that explicitly requests human review stops the pipeline. A
`skip_review` decision at or above the skip threshold skips review.

Other decisions start standard review; decision scores below the review threshold
or with unavailable provenance start deep review. A `no_concerns` review at or
above the review threshold completes the advisory review if no earlier review
reported concerns. Otherwise standard review escalates to deep review, and
unresolved deep review requires human review. Provider exceptions and malformed
responses require human review; exception text is excluded from reports.

Report schema version `"1"` includes:

- `advisory_only: true`, repository, PR number, base/head identifiers.
- `outcome`: `skipped`, `reviewed`, or `needs_human_review`.
- `route`: `no_review`, `standard`, `deep`, or `human`.
- Reasons, coverage, optional decision, and executed review stages.
- Deduplicated findings capped by policy and a unique `findings_omitted` count.

Stage finding lists are also capped individually. Escalation examines the complete
provider results before capping. Findings from earlier stages cannot be silently
discarded by a later clear response.

## Mock behavior

The decision mock skips only modified prose documentation where every changed line
corrects a repeated adjacent word. Source files, additions/removals, changed
formatting syntax, and other documentation changes are reviewed. This deliberately
narrow rule is a demonstration and cannot establish semantic safety.

The review mock reports a synthetic medium-severity concern when an added line
contains `MOCK_REVIEW_CONCERN`. It clears recognized editorial corrections and
returns uncertainty for everything else. Both depths use the same deterministic
rules and clearly label confidence as mock-derived.

## CLI and acceptance

`pr-review-router review --input PATH [--config PATH] [--output PATH]` returns JSON.
Help/version work without credentials. Successful advisory runs exit 0; invalid
input/policy or I/O exits 2. File writes are atomic, and validation failures leave
an existing report untouched. Evidence/policy cannot be overwritten by the report.

A fresh checkout installs from `uv.lock`, passes Ruff, formatting, codespell,
pytest, packaging, CLI smoke checks, and fresh-wheel execution without development
dependencies or model credentials. CI is read-only and uses verified action pins.
