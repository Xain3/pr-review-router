# Repository instructions

Follow `CONTRIBUTING.md`, `README.md`, and `docs/specification.md` as the
authoritative project guidance. Preserve established contracts and terminology;
do not describe the mock providers as performing substantive code review.

## Code and Tests

- Use Python 3.12+ and `uv`. Keep application code in `src/pr_review_router/`
  and meaningful behavior tests in `tests/`.
- Keep tests deterministic and offline. Do not add credential requirements or
  paid inference to the default test suite.
- For behavior changes, update the specification, relevant examples, and README
  so they remain consistent with the implementation.
- Follow the Python docstring convention below. When dependencies change,
  update and commit `uv.lock`.

## Product Invariants

- Reports are advisory only: the router must not approve, merge, or otherwise
  change a pull request.
- Preserve confidence provenance, patch-coverage checks, conservative
  escalation, and provider independence. Incomplete or unresolved cases must
  not be treated as safe automated acceptance.
- Keep deterministic policy checks distinct from provider assessments, and
  preserve the decision-provider and review-provider roles.

## Data and Validation

- Use sanitized fixtures in `examples/`; never commit credentials, raw pull
  request exports, or private reports. Keep runtime output under `reports/`.
- Run the relevant checks from `CONTRIBUTING.md` for the change, including
  focused tests where practical, and report which checks actually ran.
- Use Conventional Commits for commit messages when a commit is requested.
- Do not select a project license without owner direction.

## Python docstrings

When writing or editing Python files (`*.py`), follow the project's
reStructuredText docstring convention in
`.github/instructions/python-docstrings.instructions.md`:

- Start callable docstrings with a concise summary, then use reStructuredText
  fields for details: `:param name:`, `:returns:`, and
  `:raises ExceptionType:` as applicable.
- Omit `self` and `cls` from parameter fields. Keep types in Python annotations
  instead of duplicating them with `:type:` or `:rtype:` fields.
- Prefer reStructuredText fields over Google- or NumPy-style sections so
  Pylance can surface parameter descriptions in function hovers and signature
  help.
