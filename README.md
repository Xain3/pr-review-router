# pr-review-router

An advisory pull request review router with a working offline, mock-provider MVP.
It accepts exported PR evidence, checks patch coverage, selects a review path,
and emits a structured JSON report. Reports never approve, merge, or change a PR.

The included providers are deterministic demonstration rules. Their confidence
scores have `source: "mock"` and are not calibrated probabilities. Substantive
changes require human review because the mocks cannot assess their correctness.

## Install and run

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Xain3/pr-review-router.git
cd pr-review-router
uv sync --locked
uv run pr-review-router --help
uv run pr-review-router --version

# A narrow editorial correction skips review.
uv run pr-review-router review --input examples/evidence.json --config examples/policy.toml

# A synthetic concern escalates through standard and deep review to a human.
uv run pr-review-router review --input examples/concern.json --output reports/concern.json
```

No API credentials or inference network access are needed. Dependency installation
may require network access. `--config` is optional; the default policy matches
[examples/policy.toml](examples/policy.toml). JSON goes to stdout unless `--output`
is given. File output creates parent directories and replaces the destination
atomically; POSIX files are private to their owner. Input and policy paths cannot
be used as the output destination.

Exit status is 0 for every successful advisory report, including
`needs_human_review`. Invalid evidence, policy, arguments, or file I/O produce
status 2 and a concise diagnostic on stderr. Consumers should inspect the
report's `outcome`, rather than treating CLI success as permission to merge.

## Routing behavior

1. Missing, truncated, malformed, or inconsistent patches; incomplete file lists;
   empty evidence; and exceeded input/file budgets go directly to human review.
2. A validated decision can skip review only at the policy's skip threshold.
   A decision that explicitly requests human review stops the pipeline.
3. Other decisions run standard review. Low or unavailable decision confidence
   starts directly at deep review.
4. Uncertainty, concerns, or low review confidence trigger deep review. Unresolved
   concerns, uncertainty, or provider failures require human review.

The mock decision provider recognizes only duplicate-word corrections in simple
prose `.md`/`.txt` changes. The mock reviewer produces synthetic findings for
`MOCK_REVIEW_CONCERN` in added lines. All other changes remain uncertain. These
rules demonstrate routing; they do not perform a semantic code review.

Reports include commit identifiers, coverage issues, decision provenance, review
stages, routing reasons, deduplicated findings, and the number of omitted findings.
The `advisory_only` flag is always true.

## Development checks

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run codespell
uv run pytest
uv build
uv run pr-review-router --help
uv run pr-review-router --version
uv run pr-review-router review --input examples/evidence.json --config examples/policy.toml
```

Use `uv run ruff format .` to format edits. CI also validates an installed wheel
in a fresh virtual environment.

See the [specification](docs/specification.md),
[implementation handoff](docs/implementation-handoff.md),
[examples](examples/README.md), and [contribution guide](CONTRIBUTING.md).
Real Jev/Typesafe and OpenAI-compatible providers, PR fetching/posting, and a
consumer GitHub/Docker Action are future work.

Licensing is pending owner selection.
