# Repository instructions

Follow `CONTRIBUTING.md`, `README.md`, and `docs/specification.md` as the
authoritative project guidance. Preserve established runtime contracts while
distinguishing current behavior from the intended PR communication check.

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
- The intended check evaluates title/description quality, consistency between
  them, and accuracy against changes. Patches are comparison evidence; code
  correctness review is outside the intended product scope.
- Acceptance for the check requires all applicable gates to pass with no
  unresolved relevant evidence. Suggestions cannot override these conditions.
- Preserve confidence provenance, patch-coverage checks, conservative
  handling of uncertainty, and provider independence. Current legacy escalation
  behavior remains until a separately specified runtime migration.
- Keep deterministic policy checks distinct from provider assessments, and
  distinguish the decider from the planned reporter. The reporter explains a
  finalized report and cannot change the decision, confidence, or CI status.
- `ReviewProvider`, `--reviewer`, review depths, and review-driven escalation
  describe legacy runtime behavior; they are not an implemented reporter.
  Mocks remain demonstrations and do not perform substantive code review.

## Data and Validation

- Use sanitized fixtures in `examples/`; never commit credentials, raw pull
  request exports, or private reports. Keep runtime output under `reports/`.
- Preserve synthetic corpus evidence, annotations, family splits, and reference
  history. Reuse communication labels for a separately versioned benchmark;
  legacy supplied reviews and review-coverage criteria are not core requirements.
  Reference annotations must stay out of model inputs and remain provisional
  until human validation.
- Run the relevant checks from `CONTRIBUTING.md` for the change, including
  focused tests where practical. Suggested checks include `uv run pyright`;
  report which checks actually ran.
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
