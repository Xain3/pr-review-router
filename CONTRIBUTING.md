# Contributing

Use Python 3.12+ and uv. Create a feature branch, implement changes under
`src/pr_review_router/`, and add meaningful behavior tests under `tests/`.
Keep the [specification](docs/specification.md), examples, and README in sync with
user-visible behavior.

## Product scope and compatibility

The intended product checks PR title/description quality, consistency between
them, and accuracy against supplied changes. Deterministic checks and semantic
decider assessments feed rulesets and confidence requirements. Acceptance for
this check requires all applicable gates to pass without unresolved relevant
evidence. Non-blocking suggestions cannot override a failed or unresolved gate.
Code correctness review is outside this scope.

The separate reporter explains the finalized assessment, including early failures
and qualified interpretations of likely issues. It cannot change outcomes,
confidence, or CI status. Current `ReviewProvider` implementations assess diffs
and can drive escalation; they are legacy behavior, not the reporter. Preserve
their API names, configuration, prompts, schemas, and recordings until a
separate runtime migration. Legacy CLI status 0 means a report was generated;
`assess` distinguishes accepted (0), accepted with suggestions (3), rejected (1),
unresolved (4), and command errors (2). Consumer CI must explicitly map 3 to
success with warnings. The new `assess` runtime returns final
communication reports without a reviewer or reporter. See the
[target architecture](docs/specification.md#intended-pr-communication-check).

The synthetic corpus and its title/description annotations remain reusable.
Preserve its case identities, family splits, reference versions, and provenance.
Document communication-benchmark changes under a separate version, exclude
legacy review-coverage requirements there, and keep reference labels out of model
inputs. Extend labels only for missing required criteria; retain provisional
status until human validation.

## Python docstrings

Use reStructuredText fields in callable docstrings so Pylance can surface
parameter descriptions in function hovers and signature help. Use `:param name:`
for parameters, `:returns:` for meaningful results, and `:raises ExceptionType:`
for documented exceptions. Omit `self` and `cls`; keep types in Python
annotations instead of duplicating them with `:type:` or `:rtype:` fields.
Prefer these fields over Google- or NumPy-style sections.

## Checks

```sh
uv sync --locked
uv run pyright
uv run ruff check .
uv run ruff format --check .
uv run codespell
uv run pytest
uv build
uv run pr-review-router --help
uv run pr-review-router --version
uv run pr-review-router assess --input examples/pr-text/valid.json --assessment-rubric examples/pr-text/assessment.toml
uv run python benchmarks/synthetic-prs/benchmark_communication.py --split evaluation
uv run pr-review-router review --input examples/diff/evidence.json --config examples/diff/policy.toml
uv run pr-review-router evaluate --corpus examples/local-models/corpus.json --providers-config examples/local-models/replay.toml --replay-dir examples/local-models/recordings --output-dir reports/replay-evaluation
uv run pr-review-router evaluate --corpus examples/rubric-review/corpus.json --providers-config examples/local-models/replay.toml --replay-dir examples/rubric-review/recordings --output-dir reports/rubric-review-replay
uv run python examples/rubric-review/compare.py
uv run python benchmarks/synthetic-prs/validate.py
uv run python benchmarks/synthetic-prs/review_report.py --split all
```

Use `uv run ruff format .` to format changes. Commit `uv.lock` whenever dependencies
change. CI also exercises the installed wheel from a fresh environment.
Wheel validation uses runtime dependency constraints exported from the same lockfile.

Keep tests deterministic and offline; do not introduce credential requirements
or paid inference into the default suite. Preserve confidence provenance,
coverage checks, conservative escalation, advisory-only reports, and provider
independence.

Local adapter tests and corpus replay use explicitly synthetic HTTP tapes.
Live inference is an explicit developer command, never a default CI check.
Refresh tapes explicitly and inspect sanitized requests/responses before
committing. Reference labels require human review before quality assessment or
calibration; see [local model guidance](examples/local-models/README.md).

Never commit credentials, raw PR exports, or private reports. Use sanitized
demonstration fixtures in `examples/`, benchmark datasets in `benchmarks/`, and
ignored runtime output under `reports/`.

Commit your work, push the feature branch, and open a PR describing the behavior
change and checks actually run. Licensing is pending owner selection; do not
select a license without owner direction.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) for commit
messages, in the form `type(scope): description`; the scope is optional. Keep
the description concise and imperative. When the purpose or impact is not
straightforward, include a commit body explaining the rationale and summarizing
the changes. Clearly report breaking changes with `!` after the type or scope
and a `BREAKING CHANGE:` footer describing the incompatibility.
