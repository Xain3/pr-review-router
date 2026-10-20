# Router quickstart: export evidence and run the current pipeline

Export a GitHub PR and read the current router's advisory report. The intended
product checks title/description quality, consistency between them, and accuracy
against changes. The existing diff-review stages are legacy behavior; the
`assess` command emits the final communication verdict without running those
stages. Reporter generation and consumer CI enforcement remain planned. See the
[target architecture](specification.md#intended-pr-communication-check).

The router currently consumes JSON exports; PR collection is an external step.
It does not post comments, approve, merge, or change the PR.

## 1. Install the router

Use Python 3.12+, Git, and [uv](https://docs.astral.sh/uv/). Run router commands
from this checkout so it loads the supplied `models.toml`:

```sh
git clone https://github.com/Xain3/pr-review-router.git
cd pr-review-router
uv sync --locked
uv run pr-review-router --help
```

## 2. Export the PR evidence

The following **Bash** recipe uses the separately installed GitHub CLI (`gh`)
and `jq`. Authenticate with `gh auth login` using an account with read access to
the target repository. Replace `OWNER/REPO` and `123` with your PR's repository
and number. No checkout or execution of the PR's code is required.

Run the block in Bash from the router checkout:

```bash
set -euo pipefail
repo='OWNER/REPO'
pr=123
mkdir -p reports/pr-$pr

gh api "repos/$repo/pulls/$pr" > "reports/pr-$pr/before.json"
gh api --paginate --slurp "repos/$repo/pulls/$pr/files?per_page=100" \
  > "reports/pr-$pr/file-pages.json"
gh api "repos/$repo/pulls/$pr" > "reports/pr-$pr/after.json"

jq -e -n \
  --arg repo "$repo" \
  --slurpfile before "reports/pr-$pr/before.json" \
  --slurpfile after "reports/pr-$pr/after.json" \
  --slurpfile pages "reports/pr-$pr/file-pages.json" '
  $before[0] as $b | $after[0] as $a |
  ($pages[0] | add // []) as $files |
  if ([$b.base.sha, $b.head.sha, $b.title, $b.body, $b.changed_files] !=
      [$a.base.sha, $a.head.sha, $a.title, $a.body, $a.changed_files]) then
    error("PR changed during export; rerun the collection block")
  else {
    repository: $repo, number: $a.number,
    base_sha: $a.base.sha, head_sha: $a.head.sha,
    title: $a.title, body: ($a.body // ""),
    files_complete: (($files | length) == $a.changed_files),
    files: [$files[] | {
      path: .filename,
      status: (if .status == "changed" then "modified" else .status end),
      additions, deletions, patch: (.patch // null),
      patch_truncated: false
    }]
  } end' > "reports/pr-$pr/evidence.json"
```

The [GitHub files API](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests-files)
returns at most 3,000 files. The recipe uses
[CLI pagination](https://cli.github.com/manual/gh_api) and compares the exported
count with the PR's changed-file count; a mismatch sets `files_complete: false`.
The before/after check detects changes during collection, but cannot guarantee
an atomic snapshot. Collect again if the PR is being updated.

GitHub patches may be missing or incomplete. `patch_truncated: false` here means
this recipe has no explicit truncation signal; it does not certify completeness.
The router checks patch hunk counts and addition/deletion metadata. Missing,
binary, truncated, metadata-only, or inconsistent patches require human review
by default. If your collector knows a patch is truncated, set the flag to true.
Do not omit problematic files to get a passing report. Unsupported file statuses
produce validation errors and need manual handling.

Keep exports under `reports/`; they can contain private source and PR text.
Do not commit them. The [evidence contract](specification.md#evidence-contract)
is the reference if you already have your own exporter.

## 3. Assess communication quality

Supply an explicit, non-empty rubric. The following format-only example uses
no model and emits the final `accepted`, `rejected`, or `unresolved` verdict:

```sh
uv run pr-review-router assess --input reports/pr-123/evidence.json --assessment-rubric examples/pr-text/assessment.toml --output reports/pr-123/assessment.json
```

For semantic quality and consistency checks, use
`benchmarks/synthetic-prs/communication/rubric.toml` and an explicitly selected
`--decider`. The default offline mock leaves unsupported semantic criteria
unresolved. No reviewer configuration or review server is required. All
applicable gates must pass without unresolved relevant evidence; suggestions
are separate and non-blocking. Reports are advisory and do not authorize
external automation. `assess` exits `0` for accepted without suggestions, `3`
for accepted with suggestions, `1` for rejected, `4` for unresolved, and `2` for
command/configuration/input/replay/output errors. CI should treat `0` and `3`
as passing, displaying warnings for `3`; all other codes block acceptance.
A bare shell step treats `3` as failure, so capture and map the status explicitly.
The JSON retains the verdict, suggestions, and evidence for the check summary.

For a shell-based CI step, capture the status before mapping it:

```sh
status=0
uv run pr-review-router assess --input reports/pr-123/evidence.json --assessment-rubric examples/pr-text/assessment.toml --output reports/pr-123/assessment.json || status=$?
case "$status" in
  0) exit 0 ;;
  3) echo "PR communication accepted with suggestions; see assessment.json"; exit 0 ;;
  *) exit "$status" ;;
esac
```

Python callers can use `assess_pull_request(evidence, policy, rubric)` from
`pr_review_router.assessment_engine` for offline formal assessment, or
`run_assessment` from `pr_review_router.assessment_runtime` with decider settings
for semantic assessment, recording, and replay. See the
[assessment contract](specification.md#communication-assessment-runtime).

### Legacy routing workflow

Start with offline mocks to check the export and routing plumbing. For PR 123:

```sh
uv run pr-review-router review --input reports/pr-123/evidence.json --decider mock --reviewer mock --output reports/pr-123/report.json --experiment-output reports/pr-123/experiment.json
```

Replace `123` in these paths with the exported PR number. Ordinary code changes
usually require human handoff with the mocks; this reflects their limited
legacy routing rules, not a judgment that the PR's communication is poor. Mock
confidence has `source: "mock"` and is not calibrated. The mock review provider
is not a report-consuming reporter and cannot perform substantive code review.

### Optional live legacy experiment

Decision adapters and semantic rubrics remain useful for communication checks.
The current `local` profile additionally runs an Ollama code-defect assessment,
which is outside the intended product scope and does not implement the reporter.
To reproduce that legacy experiment, install and start the servers in
[local model setup](../examples/local-models/README.md#live-local-evaluation).
The supplied profile expects Ollaya with `winnow:e4b` on port 11435 and Ollama
with `pr-review-local:latest` on port 11434. Adjust `models.toml` to your installed
models; the router does not download models or start servers. Inspect their
inventories with `ollaya list` and `ollama list`.

```sh
uv run pr-review-router review --input reports/pr-123/evidence.json --profile local --output reports/pr-123/report.json --experiment-output reports/pr-123/experiment.json
```

Explicit model selection enters experiment mode: skip recommendations are
shadowed by continued legacy review, and direct acceptance/rejection policies
must be disabled. Unresolved assessments remain advisory human handoffs. See
[model selection](../README.md#named-models-and-profiles) and
[hosted Jev setup](../README.md#hosted-jev-decisions) for current adapter options.

### Apply your repository's policy

`--config PATH` selects a TOML policy, independently of model configuration.
For example, save this as `reports/quickstart-policy.toml`:

```toml
skip_confidence = 0.95
review_confidence = 0.85
max_files = 100
max_findings = 5
title_format = "conventional_commit"
required_body_sections = ["Summary", "Testing"]
```

Apply it to the exported PR (a format failure hands the PR to a human):

```sh
uv run pr-review-router review --input reports/pr-123/evidence.json --decider mock --reviewer mock --config reports/quickstart-policy.toml --output reports/pr-123/report.json --experiment-output reports/pr-123/experiment.json
```

Without `--config`, policy loading uses `PR_REVIEW_ROUTER_CONFIG` when set,
otherwise packaged defaults. Per-setting `PR_REVIEW_ROUTER_<SETTING>` environment
values override the selected file (for example, `PR_REVIEW_ROUTER_SKIP_CONFIDENCE`).
An explicit config selects a replacement file rather than merging with the
packaged file; omitted policy fields use model defaults. Title/body format
checks are deterministic policy checks, separate from provider assessments.
See the [specification](specification.md) for all fields and constraints.

## 4. Integrate with the CLI or Python API

### Command-line interface

```sh
uv run pr-review-router review --input reports/pr-123/evidence.json --decider mock --reviewer mock
```

JSON is written to stdout unless `--output PATH` is supplied. Diagnostics go to
stderr. Exit status **0** means an advisory report was produced, including a
human handoff; status **2** means invalid arguments, evidence, configuration, or
file I/O. Consumers must inspect `outcome` and the report details.

### Python API

There is no router HTTP server or REST endpoint. The engine accepts validated
evidence, a policy, and separate decision/review providers. Save this snippet as
`reports/quickstart_api.py` and run `uv run python reports/quickstart_api.py`:

```python
from pathlib import Path

from pr_review_router.config import load_policy
from pr_review_router.contracts import PullRequestEvidence
from pr_review_router.engine import review_pull_request
from pr_review_router.providers import MockDecisionProvider, MockReviewProvider

evidence = PullRequestEvidence.model_validate_json(
    Path("reports/pr-123/evidence.json").read_text(encoding="utf-8")
)
report = review_pull_request(
    evidence=evidence,
    policy=load_policy(None),
    decision_provider=MockDecisionProvider(),
    review_provider=MockReviewProvider(),
)
print(report.model_dump_json(indent=2))
```

This minimal Python example explicitly uses mocks to demonstrate the engine API;
the optional live command above reproduces the legacy model experiment.
`load_policy(None)` loads packaged defaults and per-setting environment overrides;
select a policy file by passing its path. CLI catalog/profile resolution is
separate from this engine call. The engine returns a `ReviewReport`; it does not
write a file. Evidence and policy
validation errors raise exceptions in Python rather than CLI exit codes.

A `DecisionProvider` recommends a route. The legacy `ReviewProvider` assesses
the diff at `standard` or `deep` depth and may influence escalation. Both depths
use the same interface. It does not receive the finalized assessment report and
must not be treated as the intended reporter API. See the current
[provider contracts](../src/pr_review_router/providers.py) and
[data contracts](../src/pr_review_router/contracts.py).

## 5. Act on the report

Reports use `schema_version: "4"` and always set `advisory_only: true`.

| Field | What to inspect |
| --- | --- |
| `repository`, `number`, `base_sha`, `head_sha` | Evidence identity and supplied commit identifiers. |
| `outcome`, `route`, `reasons` | Final advisory result, path taken, and explanations. |
| `coverage` | Completeness, valid patch counts, input size, and coverage issues. |
| `pr_text`, `nonsemantic_checks`, `rubric` | Deterministic policy checks, including whether enabled. |
| `decision` | Routing recommendation, provider, reason, and confidence provenance; may be null when preflight stops routing. |
| `reviews` | Ordered stages with depth, outcome, confidence provenance, summary, and findings. May be empty. |
| `findings`, `findings_omitted` | Deduplicated findings capped by policy, and the number omitted. |

| `outcome` | Meaning |
| --- | --- |
| `skipped` | Review was skipped under the configured policy. |
| `reviewed` | Review cleared under the configured policy. |
| `needs_human_review` | Evidence or automated assessment needs human resolution. |
| `accepted`, `rejected` | Advisory direct decision or configured unresolved rejection; inspect `route` and `reasons`. |
| `feedback` | Non-blocking review feedback under an explicit policy. |

Reports never approve, merge, or change a PR. The table describes current
advisory routing, not the intended CI acceptance contract. Status 0 means report
generation succeeded, including rejection and unresolved results. Confidence
must be interpreted with its source; unavailable confidence cannot clear the
current review stages.

The intended communication check accepts only when all applicable gates pass
with no unresolved relevant evidence, under the configured rules and confidence
requirements. Non-blocking suggestions cannot override a failed or unresolved
gate. That acceptance applies only to this check; it is not a GitHub approval or
permission to merge. The reporter explains the finalized result and cannot
change the CI status. `assess` now supplies the finalized machine report;
reporter generation and consumer CI enforcement remain future work.

File output creates parent directories and replaces the destination atomically.
Input and policy files cannot be output destinations. Keep private evidence and
generated reports out of version control; use `reports/` for runtime output and
sanitized fixtures in `examples/`.

Before acting, compare the report's `head_sha` and `base_sha` with the current
PR. If either changed, export and assess again. Start with `coverage.issues`
and `reasons`, plus enabled `pr_text` and `rubric` checks, to distinguish an
incomplete export from an assessed communication failure. In legacy experiments,
`reviews` and `findings` describe the separate diff-assessment stages. A handoff
may reflect a provider limitation, uncertainty, or a policy gate; it is not proof
of a code defect or poor PR communication.

`report.json` contains the advisory result. `experiment.json` contains separate
provider/configuration provenance and original decisions, useful when a shadowed
skip differs from the final route. Keep both with the evidence for the reviewed
commit. Neither replaces repository tests or a code-review decision.

For an optional offline installation check, use the
[small example](../README.md#install-and-run). For adapter testing and evaluation
outputs (`<case-id>.report.json`, `<case-id>.experiment.json`, and `summary.json`),
see [offline replay](../examples/local-models/README.md#offline-example).
