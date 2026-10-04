# Contributing

Use Python 3.12+ and uv. Create a feature branch, implement changes under
`src/pr_review_router/`, and add meaningful behavior tests under `tests/`.
Keep the [specification](docs/specification.md), examples, and README in sync with
user-visible behavior.

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
uv run ruff check .
uv run ruff format --check .
uv run codespell
uv run pytest
uv build
uv run pr-review-router --help
uv run pr-review-router --version
uv run pr-review-router review --input examples/diff/evidence.json --config examples/diff/policy.toml
uv run pr-review-router evaluate --corpus examples/local-models/corpus.json --providers-config examples/local-models/replay.toml --replay-dir examples/local-models/recordings --output-dir reports/replay-evaluation
```

Use `uv run ruff format .` to format changes. Commit `uv.lock` whenever dependencies
change. CI also exercises the installed wheel from a fresh environment.

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
fixtures in `examples/` and ignored runtime output under `reports/`.

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
