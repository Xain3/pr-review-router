# pr-review-router

The intended product checks PR title and description quality, consistency
between them, and accuracy against the supplied changes. Patches provide evidence for those comparisons. Code correctness,
security review, and implementation recommendations are outside its scope.

The implemented CLI currently accepts exported PR evidence and emits advisory
JSON reports using offline mocks or opt-in local/hosted experiments. The decider,
deterministic checks, semantic rubric, confidence provenance, and patch validation
now support the `assess` command, which returns a final communication verdict
without invoking a reviewer. Legacy `review`/`evaluate` behavior and recordings
remain available. The reporter and consumer CI integration are not implemented.
Reports never approve, merge, or change a PR.

## Terminology and roles

The intended flow is **PR evidence → deterministic checks and semantic assessments
→ rulesets and confidence requirements → finalized machine report → reporter**.
The future CI consumer enforces the machine result independently of reporter text.

| Role | Intended responsibility | Current implementation |
| --- | --- | --- |
| Decider / decision provider | Assess configured criteria against the evidence. | Decision adapters and mixed deterministic/semantic rubrics power `assess`. |
| Engine / router | Apply rulesets and confidence requirements to determine the check result. | `assess` finalizes communication gates; legacy `review` still uses review results. |
| Reporter | Explain the finalized report, distinguishing recorded facts from qualified interpretations of likely issues. | Planned; `ReviewProvider` is not a reporter. |

The reporter must cover early deterministic failures and direct rejections too.
It cannot change decisions, confidence, or CI status, invent rejection reasons,
or conduct a code review. If reporting fails, the machine result remains valid
and a deterministic explanation can render the recorded facts.

The existing **legacy review provider** (`ReviewProvider`, `--reviewer`, and
`review` model settings) receives evidence at `standard` or `deep` depth and
returns concerns or uncertainty that can drive escalation. Its optional Ollama
adapter explicitly looks for code defects. Those interfaces remain unchanged for
compatibility; they are not aliases for the planned reporter. The mock providers
are deterministic demonstrations; their `source: "mock"` confidence is not
calibrated and they cannot assess substantive correctness.

## Intended acceptance and CI behavior

Acceptance for this check requires **all applicable gates to pass with no
unresolved relevant evidence**, under the configured rules and confidence
requirements. Non-blocking suggestions can accompany passing gates; they cannot
override a failed gate or unresolved relevant evidence. Passing title syntax alone
does not resolve an accuracy check that needs a missing patch. Acceptance applies
only to this communication check and does not approve or merge the PR.

`assess` returns `accepted`, `rejected`, or `unresolved`, with suggestions separate
from the verdict. Its reports retain `automation_authorized: false`. The CLI
exits 0 whenever a valid report is generated, including rejection or uncertainty;
invalid invocation or file/configuration errors exit 2. CI enforcement is separate.
See the [target architecture and examples](docs/specification.md#intended-pr-communication-check)
and [transition increments](docs/implementation-handoff.md#subsequent-implementation-increments).

## Install and run

See the [quickstart guide](docs/quickstart.md) for exporting PR evidence, running
the current pipeline, using the CLI and Python API, and interpreting reports.
Start with a deterministic communication assessment; this example needs no model:

```sh
uv run pr-review-router assess --input examples/pr-text/valid.json --assessment-rubric examples/pr-text/assessment.toml
```

Use `--assessment-rubric examples/synthetic-prs/communication-rubric.toml` for
semantic title/description checks, and select a typed `--decider` explicitly for
live inference. Offline mocks leave unsupported semantic gates unresolved.
The commands below also retain examples of legacy review routing.

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Xain3/pr-review-router.git
cd pr-review-router
uv sync --locked
uv run pr-review-router --help
uv run pr-review-router --version

# Legacy routing: a narrow editorial correction skips review.
uv run pr-review-router review --input examples/diff/evidence.json --config examples/diff/policy.toml

# Legacy routing: a synthetic concern escalates to a human.
uv run pr-review-router review --input examples/diff/concern.json --output reports/concern.json

# Explicit PR title and body format checks.
uv run pr-review-router review --input examples/pr-text/valid.json --config examples/pr-text/policy.toml
uv run pr-review-router review --input examples/pr-text/invalid.json --config examples/pr-text/policy.toml

# Weighted rubric with hard and soft blockers.
uv run pr-review-router review --input examples/rubric/valid.json --config examples/rubric/policy.toml
uv run pr-review-router review --input examples/rubric/soft_blocker.json --config examples/rubric/policy.toml
uv run pr-review-router review --input examples/rubric/hard_blocker.json --config examples/rubric/policy.toml
```

The default mode needs no API credentials or inference network access. Dependency installation
may require network access. `--config` is optional; the default policy matches
[examples/diff/policy.toml](examples/diff/policy.toml). JSON goes to stdout unless `--output`
is given. File output creates parent directories and replaces the destination
atomically; POSIX files are private to their owner. Input and policy paths cannot
be used as the output destination.

Policy defaults are read from the packaged
[defaults.toml](src/pr_review_router/defaults.toml); model selections come from
the [model catalog](models.toml), with validated provider defaults for omitted
settings. Set
`PR_REVIEW_ROUTER_CONFIG` to select a config file when `--config` is not given.
For individual policy settings, use `PR_REVIEW_ROUTER_<SETTING>` with the
setting name uppercased, such as `PR_REVIEW_ROUTER_SKIP_CONFIDENCE=0.9` or
`PR_REVIEW_ROUTER_REQUIRED_BODY_SECTIONS='["Summary", "Testing"]'`.
Environment values override the selected TOML file. Strings may be given
unquoted; arrays and other structured values use JSON syntax. An explicit
`--config` takes precedence over `PR_REVIEW_ROUTER_CONFIG`, and a supplied
config file replaces—not merges with—the packaged defaults. Omitted CLI options
do not override configuration. Invalid values produce a usage error instead of
silently falling back. Per-setting policy flags are not currently exposed by
the CLI.

Exit status is 0 for every successful advisory report, including
`needs_human_review`. Invalid evidence, policy, arguments, or file I/O produce
status 2 and a concise diagnostic on stderr. Consumers should inspect the
report's `outcome`; CLI success is neither a passing communication gate nor
permission to merge.

## Named models and profiles

These settings describe current decider and legacy review interfaces. Selecting
a live reviewer runs the existing diff-assessment adapter, not a reporter.

`review` and `evaluate` load `models.toml` from the current working directory,
or the packaged catalog when it is absent. Use `--models-config PATH` for an
explicit catalog; it replaces the entire catalog. There is no parent-directory
search or model-setting environment override. The shipped defaults select
**offline mocks**. Changing defaults to real models opts subsequent runs into
inference. The catalog lists supported adapters with example model names, not an
inventory of installed or every externally available model.

```sh
# Explicitly select offline mocks in experiment mode.
uv run pr-review-router review --input examples/diff/evidence.json --decider mock --reviewer mock

# Live local inference: first install the models and start the local servers.
uv run pr-review-router review --input examples/local-models/editorial.json --profile local
uv run pr-review-router review --input examples/local-models/editorial.json --profile thorough --reviewer local --model-override 'review.temperature=0.2'
```

Catalog `[models.NAME]` tables contain the existing provider settings, including
`backend`. Multiple names can use the same model with different settings, such
as `local` and `local-high`. `[defaults]` selects `decider` and `reviewer` names;
`[profiles.NAME]` can replace either or both. The reviewer handles both standard
and deep depth; these are not independent reviewer models.

Each live entry declares model capabilities, for example
`capabilities = { text_generation = true, structured_output = true }`.
System One entries (`ollaya`, `jev`) require structured decisions and cannot
generate review text. LLM entries declare whether they support structured output;
that declaration is owner-supplied, not inferred from the model name or verified
against a server. Omitted declarations retain the existing adapter assumptions.

Capability checks and adapter support both apply. A decision needs structured
output; a review needs generated text. Structured-output Ollama LLMs can fill
either role. The decision adapter emulates System One's typed questions using
JSON-schema chat completions, including routing and rubric choice, probability,
and score assessments. Generated values are marked `llm_self_reported`, and task
confidence remains unavailable. They are not native model probabilities or task
calibration. Text-only review still requires a separate adapter; the current
reviewer also requires structured output. Incompatible defaults, profiles, and
CLI selections fail before inference. Capabilities cannot be setting overrides.

**Warning:** General LLM deciders are a non-optimal compatibility fallback.
Prefer a native System One decider (`ollaya` or `jev`) for the decision role.
Generated probabilities and confidence are self-reported, not native or
calibrated. Both `review` and `evaluate` print this warning once to stderr when
an LLM decider is selected, including through defaults, profiles, legacy files,
and replay. JSON output and exit status are unaffected. LLM reviewers alone
do not trigger the warning.

```sh
# Live local LLM decider and reviewer, after installing the model/server.
uv run pr-review-router review --input examples/local-models/editorial.json --profile llm
uv run pr-review-router review --input examples/local-models/editorial.json --decider local --reviewer mock --model-override 'decision.standard_max_tokens=2048'
```

LLM decisions use the shared chat settings, including `standard_max_tokens` as
the output budget for a complete batch, `context_tokens`, temperature, seed, and
reasoning effort. Decision and review settings remain independent even when the
same catalog name is selected for both; this is not independent model evidence.
The legacy provider format also accepts `backend = "ollama"` under `[decision]`;
see [the standalone LLM example](examples/local-models/llm.toml).

Precedence, from lowest to highest: catalog defaults → named `--profile` →
`--decider` / `--reviewer` → repeated `--model-override ROLE.FIELD=TOML`.
Override roles are `decision` and `review`; values use TOML syntax, so string
values need TOML quotes inside shell quotes, for example
`--model-override 'review.reasoning_effort="high"'`. Overrides replace individual
settings; duplicate keys, unknown fields/names, incompatible roles, and invalid
values are errors. Select another catalog name to change `backend` or `model`;
these identity fields cannot be overridden. Every catalog entry and profile is
validated, even when unused. Optional settings use the existing provider defaults.

The legacy `--providers-config PATH` format and behavior remain supported. It
bypasses catalog loading entirely and cannot be combined with any catalog flag,
avoiding implicit merges. Policy `--config` and its environment precedence are
unchanged. Explicit catalog selections use the existing experiment safeguards
(shadow skips, disabled direct decisions, provenance under `reports/`), even
when selecting mocks. Automatically loaded all-mock defaults retain the original
mock routing behavior. Both commands support catalog selection for replay and
recording; resolved settings, rather than profile names, determine fingerprints.
See [local setup and replay](examples/local-models/README.md).

## Routing behavior

This is the current pipeline, including legacy review stages. Its outcomes do
not yet implement the intended communication-quality CI acceptance contract.

1. Missing, truncated, malformed, or inconsistent patches; incomplete file lists;
   empty evidence; and exceeded input/file budgets go directly to human review.
2. A validated decision can skip review only at the policy's skip threshold.
   A decision that explicitly requests human review stops the pipeline.
3. Any other recommendation enters review. Low or unavailable decision
   confidence starts review at deep depth; otherwise it starts at standard
   depth.
4. Uncertainty, concerns, or low review confidence trigger deep review.
   Unresolved concerns, uncertainty, or provider failures require a human
   reviewer.

The mock decision provider recognizes only duplicate-word corrections in simple
prose `.md`/`.txt` changes. The mock review provider produces synthetic findings
for `MOCK_REVIEW_CONCERN` in added lines. All other changes remain uncertain.
These rules demonstrate routing; they do not perform a semantic code review.

Optional policy settings can also validate PR text before routing: require a
Conventional Commits title and/or non-empty body sections such as `Summary` and
`Testing`. These deterministic format checks are disabled by default; an
invalid title or body is reported under `pr_text` and handed off before review.
See the [PR-text examples](examples/pr-text/README.md) for a passing and failing
fixture. They check structure only, not whether the description is accurate.
For multiple weighted criteria, hard/soft blockers, and aggregate scores, see
the [rubric examples](examples/rubric/README.md). For the assessment-rubric
framework that combines deterministic format checks with semantic questions
about PR communication, plus a legacy supplied-review criterion, see the
[mixed formal/semantic examples](examples/rubric-review/README.md).

```sh
# Exercise deterministic and semantic rubric checks using synthetic offline replay.
uv run pr-review-router evaluate \
  --corpus examples/rubric-review/corpus.json \
  --providers-config examples/local-models/replay.toml \
  --replay-dir examples/rubric-review/recordings \
  --output-dir reports/rubric-review-replay
```

Policies can also define non-semantic checks with regular expressions or
external scripts. Regex checks target the title, body, combined patches, or
changed paths and run in a killable worker bounded by the check timeout. Script
checks receive the validated evidence JSON on standard input and pass only with
exit status 0; commands are run without a shell, and timed-out process trees are
terminated. These checks run before providers, are reported
under `nonsemantic_checks`, and fail closed to the configured human/rejected
unresolved outcome. They are deterministic gates and do not perform semantic
code review.

```toml
check_timeout_seconds = 10

[[nonsemantic_checks]]
check_id = "no-debug-marker"
description = "Do not ship debug markers."
kind = "regex"
target = "patch"
pattern = "TODO\\(debug\\)"
must_match = false

[[nonsemantic_checks]]
check_id = "external-policy"
description = "Run the repository's offline policy script."
kind = "script"
command = ["./ci/check-pr.py"]
```

The [synthetic PR corpus](examples/synthetic-prs/README.md) provides 157 fictional
PRs across 30 change families with reusable title/description annotations,
metadata, commits, complete patches, and provisional 0–4 triviality labels.
Supplied reviews and review-coverage criteria are legacy experiment dimensions.
The [separate communication benchmark](examples/synthetic-prs/README.md#communication-quality-benchmark)
uses existing canonical title/description labels under `author-communication-v1`,
preserves reference history, and withholds oracle annotations from model inputs.
Its fixed [partitions](examples/synthetic-prs/splits.json) reserve 24 independently
authored cases from 12 new families for evaluation; 133 cases are for development.
Scorers report confusion by scenario, criterion, reference triviality level, and
their intersections, with support counts and explicit uncertainty.
The [index](examples/synthetic-prs/INDEX.md) includes family and author provenance.
Both partitions have fresh blind review under the same seven-criterion rubric;
the [review guide](examples/synthetic-prs/README.md#independent-review) explains
the preserved history, current adjudications, and unresolved cases.
The existing router benchmark retains its review-coverage dimension and grades
the latest adjudicated reference revision by default. Use
`--reference-revision original` for canonical author labels, or select a registered
review snapshot with `--reference-revision NAME`; list them with
`--list-reference-revisions`. Bound predictions can be rescored offline under
another revision without inference. Unresolved judgments remain uncertain.
These synthetic AI labels still require human review before quality assessment
or calibration.

Reports include commit identifiers, coverage issues, the decision-provider
recommendation and confidence provenance, review stages and results, routing
reasons, deduplicated findings, and the number of omitted findings. The
`advisory_only` flag is always true.

Optional [direct-routing examples](examples/direct/README.md) demonstrate blocking
decider rejection, non-blocking reviewer feedback, direct decider acceptance,
and acceptance followed by feedback. Each behavior is independently configurable
and can combine with PR-text/rubric gates. Defaults preserve the existing routes.
Reports use schema version 4; acceptance/rejection remain advisory signals for
consumers and never change a PR.

## Hosted Jev Decisions

The `jev` decision backend uses the synchronous TypeSafe SDK for routing and
semantic rubric questions (`choice`, `noul`/`predicate`, and `score`). It does not
generate the planned reporter's explanations. Current experiments still select
a separate legacy review provider; the example uses a demonstration mock.
Code assessment by the optional Ollama reviewer is outside the intended scope.

See [hosted configuration and commands](examples/jev/README.md). Hosted use
requires `allow_network = true`, an environment-based API key, an HTTPS origin,
and a version-pinned model. Recording additionally requires
`allow_recording = true` and `--record`; tapes contain full evidence and must be
sanitized before sharing. Obtain owner approval of TypeSafe data handling before
sending private PR content. SDK payload logging is disabled in the router process.

Jev supports up to 255 Choice options. Complete request and per-question UTF-8
byte budgets conservatively bound input; overflow fails closed without removing
evidence. The adapter reuses its SDK client within each experiment, honors
`Retry-After` within configured retry limits, bounds responses, and retains resolved
model, SDK version, request ID, usage, and attempt counts. Hosted replay needs no
credentials or HTTP client and never falls back to live inference. Client setup
failures are recorded with zero HTTP attempts, and response-schema failures receive
the existing single strict-schema retry during live execution and replay.

Choice/Score native confidence is distribution-derived, not a probability that
a PR is safe. Noul supplies a proposition's yes-probability, not confidence.
Task confidence remains `unavailable`; all skips remain shadowed and direct
acceptance/rejection stays disabled. Rubrics may abstain on low Choice or Score
confidence, but thresholds remain experimental until validated against
human-reviewed PR labels. No hosted calibration or automated skips are enabled.

## Local models and reproducible CI

The CI commands here validate adapter integration and replay. They are separate
from the future consumer communication check; code-defect review experiments
remain legacy examples.

Use `review --providers-config PATH` to select Ollaya or structured-output
Ollama decisions and Ollama reviews independently. Provider TOML is separate from routing policy. Local
experiments shadow skip recommendations and require direct decisions to be
disabled. They retain original decisions and model provenance in separate
artifacts without changing report schema version 4.
Backend and endpoint settings are distinct from each selected model; optional
`instructions` are model-specific, while `backend_instructions` covers special
backend cases. Ollaya's `max_choice_options` defaults to 3; Ollama decisions
support up to 100 labels per question.

```sh
# Exercise the real adapters offline using explicitly synthetic HTTP tapes.
uv run pr-review-router evaluate --corpus examples/local-models/corpus.json --providers-config examples/local-models/replay.toml --replay-dir examples/local-models/recordings --output-dir reports/replay-evaluation
```

See [local model setup and evaluation](examples/local-models/README.md) for
installed-model selection, context configuration, live runs, explicit recording
refresh, and network-free replay. Default tests never require a running model or
paid inference. Replay protects integration behavior; live evaluation and
human-reviewed labels are needed to assess model quality. Local routing
confidence remains unavailable until task calibration; reviewer confidence is
self-reported.

The [synthetic PR benchmark](examples/synthetic-prs/README.md#router-rubric-benchmark)
defaults to 60 randomly selected cases, exactly two per family across all 30 families. Use
`uv run python examples/synthetic-prs/benchmark.py --seed 42` to reproduce a sample;
`--sample-size 30` for a quick run with one case per family; `--split all` runs all
157 cases. Reports
record the seed, selected case IDs, and model provenance separately for the decision
and review roles, including explicit mock-provider labels.

The [six-criterion PR and review rubric](examples/rubric-review/README.md)
combines reusable title format, Summary structure, rationale, description/diff
coverage, and testing-explanation criteria with legacy supplied-review coverage.
The supplied review is not a reporter output, and this experiment is not the
separate communication-only benchmark. It shows
blocking failures, suggestions, uncertainty, and all-pass advisory skip/approval
recommendations while preserving shadow mode. Semantic criteria support `choice`,
`noul` (also named `predicate` in rubric configuration), and ordinal `score`
questions with explicit experimental numeric bands. Ollaya sends both numeric
probability names as native `noul` questions.
Choice criteria can configure provider-visible labels that map back to the
default `passed`, `failed`, and `uncertain` routing statuses. The selected
decision provider configures its `max_choice_options` capability (default `3`).
Labels without a direct mapping can be handled by ordered, multi-criterion
assessment-rubric routing rules. Ordered enforcement rules can select which
semantic criteria apply to a scale, including any-of alternatives. Each rule can
block through `enforce` or only suggest through `soft_enforce`.
Assessment rubrics accept JSON or TOML through the same validated contract;
JSON examples are available for every question format.
Run `uv run python examples/rubric-review/compare.py` to compare all three formats
against identical labels with offline replay; `--live --record` explicitly uses
local models and saves recordings under `reports/`.

```sh
uv run pr-review-router evaluate --corpus examples/rubric-review/corpus.json --providers-config examples/local-models/replay.toml --replay-dir examples/rubric-review/recordings --output-dir reports/rubric-review-replay
```

## Development checks

```sh
uv sync --locked
uv run pyright
uv run ruff check .
uv run ruff format --check .
uv run codespell
# Full suite (all test categories)
uv run pytest
# Individual test categories
uv run pytest -m smoke
uv run pytest -m integration
uv run pytest -m unit
uv build
uv run pr-review-router --help
uv run pr-review-router --version
uv run pr-review-router review --input examples/diff/evidence.json --config examples/diff/policy.toml
```

Smoke tests check basic CLI startup and a minimal review; integration tests
exercise CLI behavior across inputs, policies, and report output; unit tests
cover isolated engine and patch-parsing logic. CI runs each category separately.

Use `uv run ruff format .` to format edits. CI also validates an installed wheel
in a fresh virtual environment.
Python callable docstrings use reStructuredText fields; see the
[docstring convention](CONTRIBUTING.md#python-docstrings).

See the [specification](docs/specification.md),
[implementation handoff](docs/implementation-handoff.md),
[examples overview](examples/README.md),
[diff-routing examples](examples/diff/README.md),
[PR-text examples](examples/pr-text/README.md), and
[contribution guide](CONTRIBUTING.md).
Local/hosted decision adapters, the assessment runtime, and the communication
benchmark are implemented. Consumer CI enforcement and reporter generation
remain separate future increments. Integrated PR collection/posting remains future
work; the current CLI does not change PRs.

Licensing is pending owner selection.
