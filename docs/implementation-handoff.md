# Implementation handoff

The mock-provider MVP is implemented. Its contracts and behavior are documented
in the [specification](specification.md); runnable inputs are in
[diff-routing examples](../examples/diff/README.md) and
[PR-text format examples](../examples/pr-text/README.md), plus
[weighted rubric examples](../examples/rubric/README.md).

## Architecture

In this document and the code, **decision provider** means the component that
recommends a route, while **review provider** means the component that assesses
the diff. These are provider interfaces, not a promise of two distinct
underlying models. The engine is the router; it applies deterministic gates and
thresholds. `standard` and `deep` identify review depth, not different provider
roles.

| Module | Responsibility |
| --- | --- |
| `contracts.py` | Strict Pydantic evidence, confidence, decision, finding, and report models |
| `config.py` | Default policy, validated TOML loading, and weighted rubric criterion contracts |
| `patches.py` | Unified hunk parsing and line tracking |
| `providers.py` | Replaceable decision/review protocols and deterministic mocks |
| `engine.py` | Coverage, PR-text, and rubric preflight; threshold routing, escalation, and finding limits |
| `cli.py` | Argument parsing, evidence loading, mock wiring, and JSON/file output |

The engine receives provider instances; it contains no mock-specific decision
rules and does not depend on provider transport SDKs. To integrate a real
decision adapter, implement `DecisionProvider.decide(evidence)` and return the
normalized `Decision` contract. To integrate a real review adapter, implement
`ReviewProvider.review(evidence, *, depth)` and return `ReviewResult`. The review
adapter receives `standard` or `deep` as a depth argument; the engine does not
require separate models for those depths.

Responses are validated by `request_decision`/`request_review` in `providers.py`:
malformed output gets one retry (passing `strict_schema=True` when the adapter
accepts it), then fails closed to human review with value-free diagnostics. Confidence provenance
must reflect the adapter's actual source.

## Review invariants

Coverage, budget, PR-text format, or rubric failures prevent provider calls.
Hard rubric blockers always fail; soft criteria affect the weighted score and
the configured overall minimum. Provider failures cannot yield a clear advisory
outcome, and exception messages do not enter reports.
Earlier review concerns are retained through escalation. Findings are deduplicated
and limited only after routing decisions; the report records omitted findings.

The mock review provider's `deep` response demonstrates the depth interface but
is not a stronger model or a second model role. Substantive changes remain
uncertain at both depths and require human review.

## Development and verification

Run the full checks in the [README](../README.md#development-checks).
Tests exercise the installed console entry point, offline example routing, budget
and coverage failures, threshold boundaries, unavailable confidence, provider
failures, finding retention/caps, schema validation, and report file handling.

CI installs locked dependencies, runs the quality suite and example reports,
builds the package, and runs the wheel from outside the checkout in a fresh
environment. Action commits were resolved from official release refs.

## Later integration work

Real Jev/Typesafe and OpenAI-compatible adapters need transport validation,
timeouts, credential handling, prompt/input isolation, and confidence provenance.
A PR evidence collector must paginate files, bind metadata and patches to supplied
commit identifiers, and mark unavailable/truncated content accurately. A consumer
GitHub/Docker Action is separate from this repository's read-only quality CI.

Keep raw exports, credentials, and private reports untracked; `reports/` is ignored.
Preserve sanitized evidence fixtures and the dependency lockfile. Licensing remains
pending owner selection. The [scaffold handoff](scaffold-handoff.md) records the
earlier foundation-only phase.
