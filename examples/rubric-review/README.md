# Six-criterion PR and review rubric

This use case evaluates a PR title, description, full diff, and separately
supplied review against six criteria. The PR has two substantive changes:
handle empty input in `average()` and raise a batch limit from 100 to 250.
This lets evaluation catch a description or review that addresses only one.

| Criterion | Assessment | Failed criterion action |
| --- | --- | --- |
| Conventional Commits title | Deterministic format check | Block |
| Populated Summary section | Deterministic structure check | Block |
| Meaningful change rationale | Model compares explanation with changes | Block |
| Description covers all substantive changes | Model compares description with all patches | Block |
| Supplied review covers all substantive changes | Model compares review with all patches | Block |
| Relevant testing explanation | Model assesses relevance to changed behavior | Suggest improvement |

The first two criteria reuse existing deterministic checks. The other four
are explicit semantic questions to the configured Ollaya decision provider.
They assess meaning, not keyword presence. A populated Rationale section saying
“These changes make things better” does not meet the rationale criterion. A
populated Testing section saying “Tests pass” triggers a suggestion.

This experiment rubric is separate from the engine's existing deterministic
policy rubric. It assesses evidence quality and review coverage, not substantive
code correctness. The independently configured review provider still assesses
the diff when routing continues. A supplied review is input evidence; it is
not substituted for a newly executed provider review.

## Routing examples

- `all-pass`: all six statuses are passed. The rubric recommends
  `skip_and_approve`, with `skip_recommended` and `approval_recommended` true.
- `invalid-title` and `missing-summary`: formal blockers fail. No model or
  metadata requests run; semantic criteria are recorded as unassessed/uncertain.
- `vague-rationale`: meaningful rationale fails despite a populated heading.
- `partial-description`: the description omits the batch-limit change, its
  rationale, and testing. Blocking failures coexist with a testing suggestion.
- `partial-review`: the supplied review addresses only the empty-input guard.
- `testing-suggestion`: the testing criterion fails, generating a concrete
  suggestion without blocking the normal review route. It is not all-pass approval.
- `missing-review`: review coverage is unavailable. Even if every other
  criterion passes, advice requires a human.
- `incomplete-patch`: preflight fails, all criteria remain unassessed, and no
  provider is called.

Known blocking failures take precedence; otherwise uncertainty requires a
human, then suggestion-only failures produce `suggest_changes`, and only all
passed criteria produce `skip_and_approve`. A high average or native confidence
cannot compensate for a blocker or unavailable criterion. There is no aggregate
score or invented task calibration.

Approval is an **advisory recommendation**, never a GitHub approval or permission
to merge. Artifacts always have `advisory_only: true` and
`automation_authorized: false`. Existing experiments retain shadow skips, so an
all-pass rubric's original skip recommendation is recorded while the actual
router continues reviewing. Neither a rubric pass nor its approval flag can
override incomplete coverage, provider failures, or unresolved code review.
Advisory report schema version 3 and policy semantics remain unchanged.

## Run offline in CI

```sh
uv run pr-review-router evaluate \
  --corpus examples/rubric-review/corpus.json \
  --providers-config examples/local-models/replay.toml \
  --replay-dir examples/rubric-review/recordings \
  --output-dir reports/rubric-review-replay
```

The checked-in recordings are explicitly **synthetic** HTTP exchanges: they
exercise real adapter requests, normalization, retry boundaries, and routing
with invented model answers. They do not claim substantive model assessment.
Reference statuses and recommendations live separately in `corpus.json` and
are marked `provisional` until a human validates them. Empty tapes verify formal
blocker and patch-preflight cases do not invoke models. Existing five-case local
model replay remains usable without refreshing its tapes.

## Run with local models

Use the setup in [local model experiments](../local-models/README.md), including
an installed Ollaya decision model and the explicit-context Ollama review alias.

```sh
uv run pr-review-router evaluate \
  --corpus examples/rubric-review/corpus.json \
  --providers-config examples/local-models/providers.toml \
  --record --output-dir reports/rubric-review-live

# Later CI/local runs can replay these exact exchanges without inference.
uv run pr-review-router evaluate \
  --corpus examples/rubric-review/corpus.json \
  --providers-config examples/local-models/providers.toml \
  --replay-dir reports/rubric-review-live \
  --output-dir reports/rubric-review-recorded-replay
```

All semantic criteria, including mixed question types, are batched in one
`/v1/systemone` request. Missing review
text is recorded as unavailable rather than asking the model to invent a review.
The complete request, including review text and instructions, must fit the
policy input budget. Native context truncation, invalid/missing/extra answers,
and transport failures cannot produce approval. Schema failures get the existing
single retry; there is no additional retry loop.

The corpus's optional `assessment_rubric` references `rubric.toml`; each case's
optional `review` references UTF-8 review text. These relative paths stay beneath
the corpus directory and are protected from output replacement. Criterion
configuration, actual instructions/schema revision, and complete review text
are part of the recording fingerprint. Editing any of them requires an explicit
fresh recording; changing reference labels alone does not require inference.

Per-case `.experiment.json` includes `assessment_rubric` with criterion statuses,
sources, unavailable task confidence for model assessments, blockers, and
suggestions. Native scores/probabilities, usage, and model metadata remain in
decision diagnostics. `summary.json` compares each status and recommendation
with reference labels and counts rubric blockers, suggestions, skip/approval
recommendations, criterion mismatches, recommendation mismatches, and unsafe
approval recommendations. A deliberately wrong all-pass answer on a blocking
reference case is an unsafe *recommendation*, even though shadow mode prevents
automated acceptance. Interpret replay metrics as integration checks; fresh
live model runs and human-reviewed labels are needed to evaluate quality.

## Compare choice, noul, and score

The same criterion IDs, evidence, reviews, and provisional reference labels can
be evaluated with different native question formats. `rubric.toml` uses choices;
`rubric-noul.toml` uses proposition probabilities; `rubric-score.toml` uses four
ordered satisfaction levels. `rubric-mixed.toml` demonstrates scores for rationale
and testing with noul for description/review coverage.

```sh
# Three formats, nine identical cases each; synthetic HTTP replay, no inference.
uv run python examples/rubric-review/compare.py

# Explicit local comparison, preserving private HTTP recordings for all formats.
uv run python examples/rubric-review/compare.py --live --record \
  --output-dir reports/rubric-type-live

# Replay that live comparison without calling models.
uv run python examples/rubric-review/compare.py \
  --providers-config examples/local-models/providers.toml \
  --replay-dir reports/rubric-type-live \
  --output-dir reports/rubric-type-recorded-replay

# Select a different rubric directly while keeping the corpus labels fixed.
uv run pr-review-router evaluate \
  --corpus examples/rubric-review/corpus.json \
  --assessment-rubric examples/rubric-review/rubric-noul.toml \
  --providers-config examples/local-models/replay.toml \
  --replay-dir examples/rubric-review/recordings-noul \
  --output-dir reports/rubric-noul-replay
```

Each semantic criterion may set `question_type = "choice"`, `"noul"`, or `"score"`.
Choices retain the explicit pass/fail/uncertain labels. Numeric formats require
explicit `fail_threshold` and `pass_threshold`; higher values must mean stronger
satisfaction. The example noul bands are fail <= 0.2 and pass >= 0.8. Scores use
four criterion-specific ordered descriptions (indices 0–3), fail <= 1.0, and
pass >= 2.75. Score `levels` must contain 2–10 distinct descriptions. All values
between the thresholds trigger human assessment. Threshold endpoints are inclusive.
There is no averaging across criteria that could cancel a blocking failure.

These thresholds are **experimental**, not tuned or task calibrated. A middle
score can represent partial satisfaction rather than epistemic uncertainty.
Noul reports the model's native probability of criterion satisfaction, not a
validated probability that an approval is correct. Task confidence remains
unavailable for every format. Compare human-reviewed label agreement, unsafe
recommendations, handoffs, schema failures, and live latency before choosing a
format or adjusting thresholds. The current provisional labels and synthetic
tapes do not establish which format is better.

`compare.py` prints a JSON comparison with totals and case details for each
format. Individual artifacts and reports are under its output directory, grouped
by format. Each criterion's `native_answer` retains the returned type and value;
choices/scores also retain their distributions and native confidence, and scores
retain their legends. Summaries expose these alongside mapped statuses and
question types. `rubric_request_seconds` measures only rubric inference HTTP
exchanges, including schema retries; `rubric_schema_failures` counts only rubric
answer validation failures. Total `request_seconds` and `schema_failures` also
include reviewer/metadata calls and are affected by routing. Replay times are
recorded durations, not fresh inference measurements. Missing review/preflight
cases remain unassessed for every format.
Changing question types, levels, thresholds, or prompts invalidates recordings.
The expanded rubric schema/prompt version required explicitly refreshing the
synthetic rubric tapes; original non-rubric local-model tapes remain unchanged.

The question shapes follow [Ollaya's API reference](https://ollaya.dev/docs/api#questions).
