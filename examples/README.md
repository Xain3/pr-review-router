# Examples

These fictional, sanitized fixtures show how `pr-review-router` evaluates
exported pull request evidence and produces an advisory report. Run commands
from the repository root after installing the project with `uv sync --locked`.
No provider credentials or inference network access are required.

## Example sets

| Directory | What it demonstrates | Detailed guide |
| --- | --- | --- |
| [`diff/`](diff/README.md) | Patch-coverage validation and review routing, from skipping a narrow documentation edit to escalating a synthetic concern for human review. | [Diff-review examples](diff/README.md) |
| [`pr-text/`](pr-text/README.md) | Optional Conventional Commits title and non-empty `Summary` and `Testing` section checks, including passing and failing inputs. | [PR-text examples](pr-text/README.md) |
| [`rubric/`](rubric/README.md) | Weighted title/body criteria, hard and soft blockers, and the resulting aggregate score and routing behavior. | [Rubric examples](rubric/README.md) |

## Run the examples

```sh
# Diff-review routing
uv run pr-review-router review --input examples/diff/evidence.json --config examples/diff/policy.toml
uv run pr-review-router review --input examples/diff/concern.json --output reports/concern.json

# PR-text format checks
uv run pr-review-router review --input examples/pr-text/valid.json --config examples/pr-text/policy.toml
uv run pr-review-router review --input examples/pr-text/invalid.json --config examples/pr-text/policy.toml

# Weighted rubric checks
uv run pr-review-router review --input examples/rubric/valid.json --config examples/rubric/policy.toml
uv run pr-review-router review --input examples/rubric/soft_blocker.json --config examples/rubric/policy.toml
uv run pr-review-router review --input examples/rubric/hard_blocker.json --config examples/rubric/policy.toml
```

The CLI prints JSON reports to stdout unless `--output` is specified. A
successful command means a report was generated; inspect its `outcome` to see
whether the evidence was skipped, reviewed, or handed off for human review.
These examples use deterministic mock providers and checks, not a substantive
code reviewer. Their reports are advisory and never approve or merge a pull
request.
