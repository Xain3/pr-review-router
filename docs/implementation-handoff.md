# Implementation handoff

The intended product checks PR title/description quality, consistency between
them, and accuracy against supplied changes. The decider, deterministic checks,
semantic assessment rubric, confidence provenance, and evidence validation remain
reusable. The [specification](specification.md#intended-pr-communication-check)
defines the intended architecture and acceptance conditions.

The `assess` runtime is implemented with direct criterion results and final
communication verdicts, without a reviewer or reporter. The legacy advisory
router remains available with mocks and opt-in local/hosted experiments. Its review stage is legacy behavior: it assesses diffs and can
influence escalation, while the optional Ollama review adapter looks for code
defects. Code correctness review is outside the intended product scope. The
planned reporter instead explains a finalized assessment and cannot change its
outcome, confidence, or CI status. The new path preserves legacy runtime
behavior, public names, prompts, fixtures, and recordings.

Runnable current inputs are in [legacy diff-routing examples](../examples/diff/README.md),
[PR-text format examples](../examples/pr-text/README.md), and
[weighted rubric examples](../examples/rubric/README.md).

Optional [direct-routing examples](../examples/direct/README.md) now demonstrate
independent acceptance/rejection permissions and thresholds, feedback depth/mode,
acceptance with feedback, and unresolved human-handoff versus blocking policy.
The report contract is version 4. Default policy preserves existing routing.

## Current architecture

The implemented `DecisionProvider` (decider) recommends a route; the legacy
`ReviewProvider` assesses the diff. Neither interface promises a distinct
underlying model. The engine applies deterministic gates and thresholds.
`standard` and `deep` identify legacy review depth, not reporter roles.

| Module | Responsibility |
| --- | --- |
| `contracts.py` | Strict Pydantic evidence, confidence, decision, finding, and report models |
| `config.py` | Default policy, validated TOML loading, and weighted rubric criterion contracts |
| `patches.py` | Unified hunk parsing and line tracking |
| `providers.py` | Decision and legacy review protocols and deterministic mocks |
| `engine.py` | Coverage, PR-text, and rubric preflight; threshold routing, escalation, and finding limits |
| `cli.py` | Argument parsing, evidence loading, mock wiring, and JSON/file output |
| `assessment_rubric.py` | Reusable criterion assessment and unchanged legacy routing reduction |
| `assessment_engine.py` | Final communication report, applicable gates, verdict, and suggestions |
| `assessment_runtime.py` | Decider-only execution, provenance, and isolated recording/replay |
| `communication_benchmark.py` | Communication-only scoring using preserved canonical annotations |

The engine receives provider instances; it contains no mock-specific decision
rules and does not depend on provider transport SDKs. To integrate a real
decision adapter, implement `DecisionProvider.decide(evidence)` and return the
normalized `Decision` contract. Existing legacy review adapters implement
`ReviewProvider.review(evidence, *, depth)` and return `ReviewResult`; this
evidence/depth interface does not supply the finalized report a reporter needs.
Do not treat it as the contract for a new reporter. Current review depths do not
require separate models.

Responses are validated by `request_decision`/`request_review` in `providers.py`:
malformed output gets one retry (passing `strict_schema=True` when the adapter
accepts it), then fails closed according to `unresolved_outcome` with value-free diagnostics. Confidence provenance
must reflect the adapter's actual source.

## Legacy routing invariants

Coverage, budget, PR-text format, or deterministic policy-rubric gate failures
prevent provider calls.
Hard rubric blockers always fail; soft criteria affect the weighted score and
the configured overall minimum. Provider failures cannot yield a clear advisory
outcome, and exception messages do not enter reports.
Earlier review concerns are retained through escalation. Findings are deduplicated
and limited only after routing decisions; the report records omitted findings.

The mock review provider's `deep` response demonstrates the depth interface but
is not a stronger model or a second model role. Substantive changes remain
uncertain at both depths. Default escalating policy requires human review;
feedback policy reports the uncertainty without requiring a human step.

## Development and verification

Run the full checks in the [README](../README.md#development-checks).
Python callable docstrings use reStructuredText fields as described in the
[contribution guide](../CONTRIBUTING.md#python-docstrings).
Tests exercise the installed console entry point, offline example routing, budget
and coverage failures, threshold boundaries, unavailable confidence, provider
failures, finding retention/caps, schema validation, and report file handling.

CI installs locked dependencies, runs the quality suite and example reports,
builds the package, and runs the wheel from outside the checkout in a fresh
environment. Action commits were resolved from official release refs.

## Subsequent implementation increments

The assessment runtime and communication benchmark are implemented alongside
legacy routing. The new public entry point is
`assess_pull_request(evidence, policy, rubric, assessment_provider=None)`; the
`assess` CLI and `run_assessment` select only the decider. A separate report
schema version 1 holds the final verdict. Legacy `review_pull_request`,
`ReviewProvider`, `--reviewer`, schema version 4, prompts, and replay remain intact.

1. **Assessment runtime and benchmark: implemented.** Complete machine reports
   include preflight failures, ordered criteria, native confidence provenance,
   selected requirements, gate statuses, blockers, suggestions, and relevant
   unresolved evidence. No reviewer or mock reporter is constructed. The
   [communication benchmark](../examples/synthetic-prs/README.md#communication-quality-benchmark)
   preserves labels and family splits under separate benchmark/reference versions.
2. **Consumer CI integration: future.** Enforce acceptance only when all
   applicable gates pass without unresolved relevant evidence. Suggestions cannot
   override those conditions. `assess` exits 0 for accepted, 3 for accepted with
   suggestions, 1 for rejected, 4 for unresolved, and 2 for command errors. CI
   must explicitly treat 3 as success with warnings. Reports retain
   `automation_authorized: false`.
3. **Reporter: future.** Consume the finalized machine assessment, covering early
   failures as well as accepted/rejected/unresolved results. Distinguish recorded
   facts from hypotheses. Reporter failure cannot change the decision or CI
   status; retain a deterministic rendering of recorded facts. No code-defect
   assessment or second vote on the decision is needed.

Benchmark validation should include inconsistent title/body pairs, omitted
changes, missing or truncated evidence, and unchanged communication judgments
when only legacy supplied review text changes. Measure criterion and decision
agreement, false acceptance, and unresolved assessments with support counts.
Label agreement does not establish calibration; human-reviewed evaluation and
task calibration still precede confidence-based automation.

Local Ollaya, structured-output Ollama, and hosted Jev decision adapters are
implemented. Preserve their confidence and experiment safeguards. See
[local experiments](../examples/local-models/README.md) and
[hosted Jev](../examples/jev/README.md) for current configuration and replay.
A future integrated PR collector must paginate files, bind metadata and patches
to commit identifiers, and accurately mark missing/truncated evidence. The
consumer check is separate from this repository's existing read-only quality CI.

Keep raw exports, credentials, and private reports untracked; `reports/` is ignored.
Preserve sanitized evidence fixtures and the dependency lockfile. Licensing remains
pending owner selection. The [scaffold handoff](scaffold-handoff.md) records the
earlier foundation-only phase.
