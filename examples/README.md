# Examples

These fictional, sanitized fixtures show how `pr-review-router` evaluates
exported pull request evidence and produces an advisory report. Run commands
from the repository root after installing the project with `uv sync --locked`.
Mock and replay examples require no provider credentials or inference network
access. Live local examples explicitly require running local model servers.

The project uses **decision provider** for the route recommender and
**review provider** for the diff assessor. `standard` and `deep` are review
depths, not separate provider roles. See the
[terminology and roles guide](../README.md#terminology-and-roles).

## Example sets

| Directory | What it demonstrates | Detailed guide |
| --- | --- | --- |
| [`direct/`](direct/README.md) | Configurable direct decider acceptance/rejection, non-blocking reviewer feedback, and combined text/rubric gates. | [Direct-routing examples](direct/README.md) |
| [`diff/`](diff/README.md) | Patch-coverage validation and review routing, from skipping a narrow documentation edit to escalating a synthetic concern for human review. | [Diff-review examples](diff/README.md) |
| [`pr-text/`](pr-text/README.md) | Optional Conventional Commits title and non-empty `Summary` and `Testing` section checks, including passing and failing inputs. | [PR-text examples](pr-text/README.md) |
| [`rubric/`](rubric/README.md) | Weighted title/body criteria, hard and soft blockers, and the resulting aggregate score and routing behavior. | [Rubric examples](rubric/README.md) |
| [`local-models/`](local-models/README.md) | Independent local adapters, shadow skips, provisional evaluation labels, and explicitly synthetic offline HTTP replay. | [Local models and replay](local-models/README.md) |
| [`rubric-review/`](rubric-review/README.md) | Six formal/semantic criteria applied to PR text and a supplied review, with blockers, suggestions, and advisory skip/approval recommendations. | [PR and review rubric](rubric-review/README.md) |

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
whether the evidence was skipped, reviewed, handed off for human review,
accepted, rejected, or returned as non-blocking feedback.
The diff, direct, PR-text, and rubric examples use deterministic demonstration
mocks and checks, which do not perform substantive code review. Local-model
examples distinguish live model experiments from synthetic replay. All reports
are advisory and never approve or merge a pull request.
