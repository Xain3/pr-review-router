# Legacy review benchmark

[Directory guide](../README.md) · [Shared corpus and annotations](../CORPUS.md)

| File | Purpose |
| --- | --- |
| `benchmark.py` | Run and score legacy routing assessments. |
| `review_report.py` | Compare canonical annotations with preserved review judgments. |
| `evaluate_triviality.py` | Score offline triviality predictions against a selected reference revision. |
| `rubric.toml` | Active legacy rubric, including supplied-review coverage and triviality enforcement. |
| `rubric_legacy.toml` | Original six-criterion predecessor for historical comparisons. |
| `policy.toml`, `providers.toml` | Pinned legacy execution settings; providers select live local inference. |

## Assessment rubric variants

`rubric_legacy.toml` preserves the original six-criterion rubric, where each
criterion's `action` determines whether failure blocks or suggests a change.
`rubric.toml` adds a choice-based `change-triviality` criterion and uses ordered
`enforcement_rules` to select blocking and soft criteria for editorial or
mechanical, bounded-behavior, and substantive or incompatible changes. The
scale follows the corpus's 0–4 triviality definitions; `uncertain` intentionally
matches no rule, so enforcement fails closed and cannot recommend approval.

Because the scale has six choices, the configured decision provider must support
at least six `max_choice_options`. Enforcement rules replace per-criterion
`action` fields; the legacy rubric retains those fields for comparison.
Classified scale labels map to `passed` and `uncertain` to `uncertain`; these
statuses only report whether a classification was made, because enforcement
rules match the effective label. The synthetic rubric maps native triviality
choices below `uncertain_below_confidence = 0.8` to `uncertain` for assessment
and enforcement. This is a conservative threshold on self-reported, uncalibrated
confidence, not a probability guarantee; the raw answer remains in the result.

## Router rubric benchmark

This existing benchmark retains supplied-review coverage as a legacy dimension.
It is separate from the [communication quality benchmark](../communication/README.md),
which reuses the corpus without making supplied review text a requirement.

`../generate.py` also writes `../corpus.json`, a router `evaluate` corpus for
`rubric.toml`. Its expected criterion statuses come from the oracle
(`not_assessable` becomes `uncertain`), and each expected recommendation is
computed by applying the rubric's own enforcement rules to those statuses and the
oracle triviality level. Those execution references remain preserved. The benchmark
recomputes grading references from **the latest adjudicated review by default**,
including revised criterion labels and unresolved triviality judgments. Explicit
[`policy.toml`](policy.toml) and [`providers.toml`](providers.toml) pin execution
to the local `winnow:e4b` Ollaya decision model with six choice options and the
deterministic mock reviewer. The benchmark measures rubric decisions; the mock
reviewer supplies synthetic fixtures and performs no substantive code review.

```sh
# Live local inference on a reproducible 60-case sample, recording HTTP exchanges.
uv run python benchmarks/synthetic-prs/legacy-review/benchmark.py --seed 42 --record \
  --output-dir reports/synthetic-prs-benchmark

# Offline replay of that recording; no inference.
uv run python benchmarks/synthetic-prs/legacy-review/benchmark.py --seed 42 \
  --replay-dir reports/synthetic-prs-benchmark \
  --output-dir reports/synthetic-prs-benchmark-replay

# Re-evaluate an explicit set of PRs with the current rubric; repeat --case-id.
uv run python benchmarks/synthetic-prs/legacy-review/benchmark.py \
  --case-id pr-001 --case-id pr-101 --reference-revision original --record \
  --output-dir reports/synthetic-prs-rationale-rereview

# Rescore the same bound summary against the latest review, offline.
uv run python benchmarks/synthetic-prs/legacy-review/benchmark.py \
  --summary reports/synthetic-prs-benchmark/summary.json \
  --output-dir reports/synthetic-prs-benchmark-rescored
```

Select grading labels independently of model inputs:

```sh
# List available revisions without inference, including the current rubric refresh.
uv run python benchmarks/synthetic-prs/legacy-review/benchmark.py --list-reference-revisions

# Compare the same bound predictions with the canonical author-intent baseline.
uv run python benchmarks/synthetic-prs/legacy-review/benchmark.py \
  --summary reports/synthetic-prs-benchmark/summary.json \
  --reference-revision original \
  --output-dir reports/synthetic-prs-original-grade

# Pin an immutable named review rather than the moving latest alias.
uv run python benchmarks/synthetic-prs/legacy-review/benchmark.py \
  --summary reports/synthetic-prs-benchmark/summary.json \
  --reference-revision review-2026-10-18-rubric-refresh \
  --output-dir reports/synthetic-prs-pinned-grade
```

The frozen `review-2026-10-18` revision remains available for reproducing older
scores; it is no longer the `latest` alias.

The standalone `evaluate_triviality.py` accepts the same `--reference-revision`
selector and defaults to `latest`. `original` means the preserved canonical author
annotations, including recorded corrections, rather than reconstructed initial
drafts. `historical-125` covers development only: select `--split development`
with matching predictions. Unknown names and missing selected-case coverage are
errors; they never fall back to author labels. The registry
[`reference-revisions.json`](../independent-review/reference-revisions.json) names
archive snapshots and their validation protocols. To add a revision, preserve old
archives, register new partition archives, and move `latest` when ready. Every
revision pins its rubric and case-input hashes. Frozen revisions remain usable
under their original rubric; `latest` must match the current rubric. Changing
inputs or rubric requires re-review and fresh predictions.

The runner removes `PR_REVIEW_ROUTER_*` environment overrides so the pinned
policy alone applies, then runs `pr-review-router evaluate` and writes
`benchmark.json` beside the router's per-case outputs. It records SHA-256
hashes of prediction inputs and separate grading sources; per-criterion
expected-to-actual counts; recommendation agreement and unsafe approvals; and
triviality accuracy from the native scale answers. Outputs stay under ignored
`reports/`.

The router benchmark defaults to `--split random --sample-size 60`. It randomly
selects in balanced family rounds without replacement: exactly two cases per
family. Use `--sample-size 30` for a quick run with exactly one per family.
Other sizes continue across families with remaining cases; samples smaller than
30 choose that many distinct families.
Without `--seed`, a fresh seed is generated; `benchmark.json` and
`benchmark-inputs.json` record `sampling.seed`, the method, size, and selected case
IDs. Repeat `--seed` and `--sample-size` to compare models on the same inputs.
Offline `--summary` rescoring automatically reuses the recorded selection and seed;
HTTP replay requires the original seed and size. The selected corpus, rubric, and
input snapshots are saved under the run directory's `sample-inputs/`.
These cross-family random samples are not family-disjoint held-out evaluations.
The standalone triviality scorer retains its evaluation default; explicit named
partitions and `--split all` remain available in both runners. Every metric
group includes its support. `by_scenario`, `by_triviality_level`, `by_family_id`,
`by_split`, `by_author_id`, and `by_scenario_and_triviality_level` each retain the
full criterion confusion. Thus a failed blocking criterion cannot disappear
inside overall agreement. Reports count false passes on failed/uncertain criteria,
missed blocks, unsafe approval recommendations, and non-trivial changes predicted
trivial. `uncertain` and `unanswered` are distinct prediction columns and stay in
denominators. Status agreement for `change-triviality` alone is insufficient:
all classified scale choices map to `passed`, so inspect the native ordinal matrix.
Unobserved intersections are omitted; present groups can have support one and
should not be interpreted as robust population estimates.

Results must contain each selected case exactly once. Version-2
`benchmark-inputs.json` captures selected evidence, commit metadata, review texts,
partitions, rubric, configuration, original run labels, and the completed summary
hash. Execution corpus paths, order, category, and rubric must match the fixed
index. The runner checks that inputs did not change during inference. Offline
`--summary` requires that binding and rejects changed inputs or results. It can
regrade unchanged predictions against new review snapshots without inference.
Version-1 bindings work when their original canonical labels remain verifiable;
unbound summaries require a fresh benchmark run.

`benchmark.json` version 4 records `grading_reference` (requested/resolved name,
protocol and source hashes) and `grading_references` (selected per-case judgments).
The raw `summary.json` retains execution references and its original accounting;
use `benchmark.json` for the chosen grade. No author annotations are rewritten.
The frozen `review-2026-10-18` grading retains 41 uncertain triviality references:
35 development and six evaluation cases. The rubric-refresh revision classifies
all 157 triviality references and retains contested criterion judgments.
Uncertain references in any revision have their own confusion row and stratum,
and cannot justify approval.
Numeric triviality accuracy uses only known reference levels, retaining
prediction abstentions in that denominator. An all-uncertain
stratum reports null numeric accuracy. `native_label_agreement` includes every
case, and counts of classifications or trivial predictions on unresolved
references expose potentially overconfident decisions. All labels remain
provisional AI judgments.

On 2026-10-15, a live run on the original 100 cases (no review text and the earlier
criterion instructions) had no schema or transport failures and no unsafe
approvals. Recommendation agreement was 0.86, with 13 oracle `block` cases
returned as `needs_human_review`. The deterministic title and Summary checks
agreed fully. `change-rationale` passed 18 oracle failures; `description-coverage`
passed 14 oracle `not_assessable` cases. Triviality was exact for 73 cases,
classified in 99, and binary trivial/non-trivial agreement was 0.95; errors were
mostly adjacent (editorial as mechanical, bounded as substantive), plus four
incompatible changes classified as mechanical. A second live run and a replay
both reproduced the benchmark exactly. That recording does not apply to the current
corpus or rubric instructions; rerun with `--record` before comparing results.

Review text is supplied for most cases, so the benchmark now exercises
`skip_and_approve` and `suggest_changes`. About a third of cases still carry no
review or an unresolvable one, which keeps `review-coverage` uncertain and the
recommendation at `needs_human_review` unless another criterion blocks. The
benchmark hash record includes a digest of all review texts. These labels remain
provisional and are not human-reviewed.


## Path migration

Runners, rubrics, policy, and providers moved here from the parent directory.
`rubric.toml` is the active legacy router rubric; `rubric_legacy.toml` is its
six-criterion predecessor. Neither is the communication rubric. Rubric contents
and frozen review archives are unchanged. The root `corpus*.json` manifests
retain their cases and labels, with only the active rubric path updated.
Old benchmark bindings hash those manifest bytes and therefore require a fresh
run; historical reports and reference revisions remain preserved.
