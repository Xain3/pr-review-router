# Mini handoff: scaffold the independent project

Create only the development foundation for `pr-review-router` in a new owner-supplied directory. Do not modify Pianoteq Remote. The [specification](specification.md) and [full implementation handoff](implementation-handoff.md) describe later work; do not implement providers, routing, PR integration, paid API calls, or a Docker Action in this task.

## Project layout

Use Python 3.12+, uv, a src layout, distribution name `pr-review-router`, and import package `pr_review_router`. Initialize Git only if the destination is not already a repository.

```text
src/pr_review_router/
  __init__.py
  cli.py
tests/
  test_cli.py
docs/
  specification.md
  implementation-handoff.md
  scaffold-handoff.md
examples/
  README.md
.github/workflows/
  checks.yml
pyproject.toml
uv.lock
.python-version
.gitignore
.editorconfig
.env.example
README.md
CONTRIBUTING.md
```

Copy the handoff documents into the new docs directory and preserve their relative links. Start with only a CLI module; add architectural modules when they have implementations rather than creating empty provider or engine files. Expose `pr-review-router --help` and `--version` using argparse. Other commands are future work; do not return simulated review results.

## Dependencies and configuration

- Initialize a uv package with Python 3.12 and initial version `0.1.0`. Keep `.python-version` at `3.12`, require Python `>=3.12`, and commit `uv.lock`.
- Add Ruff, pytest, and codespell to the development dependency group. Defer HTTPX, Pydantic, and provider SDKs until functional implementation needs them.
- Configure the console entry point in `pyproject.toml`. Read the installed package version through importlib.metadata rather than maintaining a second version constant.
- Put Ruff, pytest, and codespell configuration in `pyproject.toml`: Ruff target Python 3.12, line length 100, lint rules `E4`, `E7`, `E9`, `F`, `I`, `UP`, and `B`; pytest searches `tests/`.
- Use codespell for tracked prose and source comments. Exclude `uv.lock`, generated output, caches, virtual environments, and `.git`. Add only narrow, reviewed vocabulary exceptions such as `Jev` and `Ollaya` if actually flagged; do not disable checking whole source/docs trees.
- Add `.editorconfig` with UTF-8, LF, final newlines, trimmed trailing whitespace, four-space Python indentation, and two-space YAML/TOML indentation.
- `.env.example` contains empty `TYPESAFE_API_KEY` and `REVIEW_MODEL_API_KEY` assignments and comments saying credentials are optional during scaffolding. Do not load dotenv or require credentials for help, tests, or checks.
- Do not select a license without owner direction; document that licensing is pending.

## Git hygiene

Ignore `.venv/`, `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.ruff_cache/`, `.coverage`, `.coverage.*`, `htmlcov/`, `dist/`, `build/`, `*.egg-info/`, `.env`, `.env.*`, private runtime reports under `reports/`, and `.DS_Store`. Explicitly unignore `.env.example`.

Keep `uv.lock`, `.python-version`, configuration, sanitized examples, and handoff documents tracked. Do not globally ignore JSON: later evidence fixtures and schemas need version control. Never add raw PR exports or credentials. No mandatory local Git hooks in this initial scaffold.

## Checks, documentation, and acceptance

Create one read-only GitHub Actions job for pull requests and pushes to `main`. Use Python 3.12, install uv with an official action pinned to a verified commit, and install locked development dependencies. Do not guess action hashes; resolve them from official release refs. Disable automatic Python downloads when the configured interpreter is already installed. Run:

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run codespell
uv run pytest
uv build
uv run pr-review-router --help
uv run pr-review-router --version
```

Document the same commands in README/CONTRIBUTING, plus `uv run ruff format .` for local formatting. Explain the advisory project's future purpose, link the specification, and state that review functionality is not implemented. Put a short explanation of future sanitized inputs in `examples/README.md`; do not supply pretend production configuration.

Test CLI help/version through the actual entry point, including successful exits and agreement with package metadata. Validate packaging by installing the built wheel in a temporary environment and running help/version without development dependencies. All checks must work without model credentials or paid/network inference. Dependency installation may require network access.

Acceptance: a fresh checkout installs from the committed lockfile; lint, formatting, spell checking, tests, and packaging pass; CI runs those checks; the installed command exposes help/version; no review behavior or consumer-repository integration has been added. Report commands actually run and any failures. Do not publish releases or create commits unless separately requested.