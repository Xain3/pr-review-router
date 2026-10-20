# Examples

These fictional, sanitized fixtures show how `pr-review-router` evaluates
exported pull request evidence and produces an advisory report. Run commands
from the repository root after installing the project with `uv sync --locked`.
Mock and replay examples require no provider credentials or inference network
access. Live local examples explicitly require running local model servers.
Hosted Jev examples require explicit network consent and an environment-based
credential, and can incur API charges. They are not run by default tests.

The intended product checks title/description quality and consistency against
changes. Its **decider** performs assessments; the planned **reporter** explains
the finalized result without changing it. These examples still use the current
**legacy review provider**, whose diff assessments can affect routing.
`standard` and `deep` are legacy review depths, not reporter roles. See the
[terminology and roles guide](../README.md#terminology-and-roles).
The `assess` command now returns final communication verdicts without a reviewer.
Reporter generation and consumer CI enforcement remain future work; the legacy
commands remain compatibility examples.

## Example sets

| Directory | What it demonstrates | Detailed guide |
| --- | --- | --- |
| [`direct/`](direct/README.md) | Direct decider acceptance/rejection and legacy non-blocking review feedback; feedback does not implement the reporter contract. | [Direct-routing examples](direct/README.md) |
| [`diff/`](diff/README.md) | Reusable patch-coverage checks and legacy review routing with synthetic concerns and escalation. | [Legacy diff-review examples](diff/README.md) |
| [`pr-text/`](pr-text/README.md) | Offline `assess` rubric and legacy policy checks for title/body structure, including required gates and advisory suggestions. | [PR-text examples](pr-text/README.md) |
| [`rubric/`](rubric/README.md) | Weighted title/body criteria, hard and soft blockers, and the resulting aggregate score and routing behavior. | [Rubric examples](rubric/README.md) |
| [`local-models/`](local-models/README.md) | Reusable decision adapters and legacy code-review experiments, with shadow skips and synthetic offline replay. | [Local models and replay](local-models/README.md) |
| [`jev/`](jev/README.md) | Opt-in hosted decisions and semantic rubrics, pinned models, private recording, and credential-free replay. | [Hosted Jev guide](jev/README.md) |
| [`rubric-review/`](rubric-review/README.md) | Reusable deterministic/semantic communication checks alongside a legacy supplied-review criterion and review pipeline. | [PR and review rubric](rubric-review/README.md) |
| [`synthetic-prs/`](synthetic-prs/README.md) | 157 fictional PRs with reusable title/description annotations and family splits; supplied reviews are a legacy dimension. A separate communication benchmark uses canonical annotations without supplied reviews. | [Corpus guide](synthetic-prs/README.md) and [index](synthetic-prs/INDEX.md) |

## Run the examples

```sh
# Legacy diff-review routing
uv run pr-review-router review --input examples/diff/evidence.json --config examples/diff/policy.toml
uv run pr-review-router review --input examples/diff/concern.json --output reports/concern.json

# PR-text format checks
uv run pr-review-router review --input examples/pr-text/valid.json --config examples/pr-text/policy.toml
uv run pr-review-router review --input examples/pr-text/invalid.json --config examples/pr-text/policy.toml

# Weighted rubric checks
uv run pr-review-router review --input examples/rubric/valid.json --config examples/rubric/policy.toml
uv run pr-review-router review --input examples/rubric/soft_blocker.json --config examples/rubric/policy.toml
uv run pr-review-router review --input examples/rubric/hard_blocker.json --config examples/rubric/policy.toml

# Combined deterministic and semantic assessment-rubric checks (synthetic offline replay)
uv run pr-review-router evaluate \
  --corpus examples/rubric-review/corpus.json \
  --providers-config examples/local-models/replay.toml \
  --replay-dir examples/rubric-review/recordings \
  --output-dir reports/rubric-review-replay
```

The CLI prints JSON reports to stdout unless `--output` is specified. A
successful command means a report was generated; inspect its `outcome` to see
whether the evidence was skipped, reviewed, handed off for human review,
accepted, rejected, or returned as non-blocking feedback.
The diff, direct, PR-text, and rubric examples use deterministic demonstration
mocks and checks, which do not perform substantive code review. Local-model
examples distinguish live model experiments from synthetic replay. All reports
are advisory and never approve or merge a pull request.
