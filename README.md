# pr-review-router

An advisory pull request review router with offline mocks and opt-in local model experiments.
It accepts exported PR evidence, checks patch coverage, selects a review path,
and emits a structured JSON report. Reports never approve, merge, or change a PR.

The default mock providers are deterministic demonstration rules. Their confidence
scores have `source: "mock"` and are not calibrated probabilities. The mocks cannot assess substantive correctness. Default routing hands such
changes to humans; optional direct/feedback policies demonstrate other routes.

## Terminology and roles

The engine has two provider roles. A **decision provider** (`DecisionProvider`)
reads the PR evidence and recommends whether to skip review, start review, or
hand the PR to a person, or directly accept/reject when enabled by policy. It chooses a route; it does not assess whether the
change is correct. A **review provider** (`ReviewProvider`) assesses the diff
and returns `no_concerns`, `concerns`, or `uncertain`, with findings when
appropriate.

The **router** is the engine that validates evidence, applies policy thresholds,
calls the providers in order, and escalates unresolved results. PR-title/body
format checks and the optional rubric are deterministic policy checks that run
before providers; they are not model roles and do not establish whether a
description accurately describes the diff. A **human reviewer** handles cases
the automated route cannot safely resolve.

“Provider” names the integration interface, not necessarily the technology
behind it: the CLI defaults to offline mocks and can explicitly select local
model adapters. `standard` and `deep` are review depths passed to the same review
provider interface, not two different model roles. The mocks cannot perform a
substantive code review.

## Install and run

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Xain3/pr-review-router.git
cd pr-review-router
uv sync --locked
uv run pr-review-router --help
uv run pr-review-router --version

# A narrow editorial correction skips review.
uv run pr-review-router review --input examples/diff/evidence.json --config examples/diff/policy.toml

# A synthetic concern escalates through standard and deep review to a human.
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
[defaults.toml](src/pr_review_router/defaults.toml); model defaults remain as
hardcoded fallbacks for settings omitted from a config file. Set
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
report's `outcome`, rather than treating CLI success as permission to merge.

## Routing behavior

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
the [rubric examples](examples/rubric/README.md).

Reports include commit identifiers, coverage issues, the decision-provider
recommendation and confidence provenance, review stages and results, routing
reasons, deduplicated findings, and the number of omitted findings. The
`advisory_only` flag is always true.

Optional [direct-routing examples](examples/direct/README.md) demonstrate blocking
decider rejection, non-blocking reviewer feedback, direct decider acceptance,
and acceptance followed by feedback. Each behavior is independently configurable
and can combine with PR-text/rubric gates. Defaults preserve the existing routes.
Reports use schema version 3; acceptance/rejection remain advisory signals for
consumers and never change a PR.

## Local models and reproducible CI

Use `review --providers-config PATH` to select Ollaya decisions and Ollama
reviews independently. Provider TOML is separate from routing policy. Local
experiments shadow skip recommendations and require direct decisions to be
disabled. They retain original decisions and model provenance in separate
artifacts without changing report schema version 3.

```sh
# Exercise the real adapters offline using explicitly synthetic HTTP tapes.
uv run pr-review-router evaluate --corpus examples/local-models/corpus.json --providers-config examples/local-models/replay.toml --replay-dir examples/local-models/recordings --output-dir reports/replay-evaluation
```

See [local model setup and evaluation](examples/local-models/README.md) for
installed-model selection, context configuration, live runs, explicit recording
refresh, and network-free replay. Default tests never require a running model or
paid inference. Replay protects integration behavior; live evaluation and
human-reviewed labels are needed to assess model quality. Ollaya routing
confidence remains unavailable until task calibration; reviewer confidence is
self-reported.

The [six-criterion PR and review rubric](examples/rubric-review/README.md)
evaluates title format, Summary structure, meaningful rationale, description
coverage of the diff, supplied-review coverage, and relevant testing. It shows
blocking failures, suggestions, uncertainty, and all-pass advisory skip/approval
recommendations while preserving shadow mode. Semantic criteria support `choice`,
`noul`, and ordinal `score` questions with explicit experimental numeric bands.
Run `uv run python examples/rubric-review/compare.py` to compare all three formats
against identical labels with offline replay; `--live --record` explicitly uses
local models and saves recordings under `reports/`.

```sh
uv run pr-review-router evaluate --corpus examples/rubric-review/corpus.json --providers-config examples/local-models/replay.toml --replay-dir examples/rubric-review/recordings --output-dir reports/rubric-review-replay
```

## Development checks

```sh
uv sync --locked
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
Hosted Jev/TypeSafe and review providers, PR fetching/posting, and a
consumer GitHub/Docker Action are future work.

Licensing is pending owner selection.
