# Explanations of finalized assessments

Generate a finalized report first, then render a human-facing comment offline:

```sh
uv run pr-review-router assess --input examples/pr-text/valid.json --assessment-rubric examples/pr-text/assessment.toml --output reports/assessment.json
uv run pr-review-router report --input reports/assessment.json --output reports/comment.md
uv run pr-review-router report --input reports/assessment.json --json
```

The same reporter handles rejected, unresolved, early preflight failures, and
accepted reports with suggestions. A rejected `assess` exits 1, an unresolved
assessment exits 4, and acceptance with suggestions exits 3; the report is still
written. Run `report` separately after handling that status. `report` returns 0
when an explanation is written, including deterministic fallback. It does not
provide CI enforcement or post comments to GitHub.

`models.toml` contains two explicit experimental Ollama choices:

| Alias | Installed model identifier |
| --- | --- |
| `qwen-coding` | `qwen3.6:35b-a3b-coding-mtp-q4_K_M` |
| `qwen-general` | `qwen3.6:35b-a3b-mtp-q4_K_M` |

After installing the desired model in your own Ollama server, opt in explicitly:

```sh
uv run pr-review-router report --input reports/assessment.json --reporter-config examples/reporter/models.toml --reporter qwen-coding --output reports/comment.md
uv run pr-review-router report --input reports/assessment.json --reporter-config examples/reporter/models.toml --reporter qwen-general --json
```

There is no default live model. The server must use a loopback HTTP(S) origin.
The model receives only a finalized report snapshot and recorded facts, never
a decision callback or raw patches. It orders facts and selects a qualified
clarification template for recorded uncertainty. The renderer owns verdict and
reason text. Both modes include proposed solutions: deterministic remedies for
recorded failures/uncertainty and optional reasoned model advice with concrete
steps and a rationale. Model proposals must reference actionable recorded facts;
they cannot become new findings or change the verdict. Invalid, incomplete, or
unavailable responses fall back to the exact offline Markdown. JSON output
records the mode, safe diagnostic, and original assessment fingerprint.

Python callers can use `explain_assessment(report)` for deterministic rendering
or `explain_assessment(report, OllamaReporter(settings))` for explicit generation
from `pr_review_router.reporter`. This role is separate from deciders and legacy
`ReviewProvider` / `--reviewer` adapters. It cannot change the assessment,
confidence, gates, CI meaning, or PR state, and does not run tests or review code.

For example, a deterministic title-format remedy can read:

> Rewrite the PR title as type(scope): description, choosing a type, optional
> scope, and concise description that match the actual changes.

A model proposal for recorded description-accuracy uncertainty might read:

> Compare each Summary claim with the supplied changes. Add an explanation
> linking each claim to the affected behavior; ask the author to clarify any
> claim you cannot substantiate.
>
> Rationale: Connecting claims to changed behavior may help resolve the recorded
> accuracy uncertainty without assuming a defect.

That model example is illustrative, not a live inference result. Both are advice
for the author to verify, not promises of acceptance. Deterministic baseline
remedies are retained alongside model advice. JSON exposes each action separately:

```json
{
  "fact_ids": ["fact:2"],
  "steps": ["Compare each Summary claim with the supplied changes."],
  "rationale": "This may help clarify the recorded accuracy uncertainty.",
  "source": "model",
  "proposed": true
}
```

The full output wraps these objects in `next_steps` alongside `markdown` and the
assessment fingerprint. They do not change the machine assessment's suggestions,
confidence, gates, or CI meaning. When no issue or suggestion is recorded, the
report proposes no corrective action.
