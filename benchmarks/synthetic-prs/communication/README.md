# Communication benchmark

[Directory guide](../README.md) · [Shared corpus and annotations](../CORPUS.md)

## Communication quality benchmark

All 157 cases carry reusable title/description annotations. The separate
`communication-v1` benchmark runs the new `assess` runtime and scores its final
communication verdict using canonical author labels under
`author-communication-v1`. The existing router benchmark, supplied reviews, and
review-reference archives remain unchanged. Reporter generation and consumer CI
enforcement are not implemented.

```sh
# Offline mocks: formal criteria run; unsupported semantics remain unresolved.
uv run python benchmarks/synthetic-prs/communication/benchmark.py --split evaluation

# Explicit live decision inference after installing/configuring the selected model.
uv run python benchmarks/synthetic-prs/communication/benchmark.py --split development --decider winnow --record --output-dir reports/communication-live

# Exact assessment-only replay, with no live fallback.
uv run python benchmarks/synthetic-prs/communication/benchmark.py --split development --decider winnow --replay-dir reports/communication-live --output-dir reports/communication-replay
```

[`rubric.toml`](rubric.toml) selects these existing canonical annotation IDs:

| Assessment | Criterion IDs | Enforcement |
| --- | --- | --- |
| Title syntax and meaningfulness | `title_format`, `title_meaningful` | Required |
| Title agrees with patches | `title_diff_consistency` | Required |
| Substantive description | `description_present` | Required |
| Title and description agree | `description_title_consistency` | Required |
| Description covers every substantive patch change | `description_diff_consistency` | Required |
| Concrete motivation | `rationale` | Required |
| Relevant validation explanation | `testing` | Advisory |

The default split is the preserved 24-case evaluation partition. Development
contains 133 cases; `--split all` selects all 157. Family assignments and matched
variants stay intact. Model inputs contain only evidence and criterion questions:
no supplied `review.md`, annotations, scenario tags, expected outcomes, reference
summaries, or other oracle metadata. Reviewing labels and changing review text
cannot influence assessment fingerprints or communication decisions.

Outputs include per-case `*.assessment.json` and `*.provenance.json`, optional
`*.tape.json`, and `summary.json`. The summary records rubric, split, and reference
hashes; criterion confusion/agreement; final decision agreement; false acceptance;
unresolved assessments; family/scenario support; and each case's expected and
actual statuses. Criterion metrics compare against independently normalized
annotation statuses; final expected verdicts still come from the assessment
engine. Original reference statuses remain visible. `not_assessable`, `uncertain`,
and `not_applicable` are never converted into passing gates: without an explicit
applicability rule, they score as unresolved. Reports must be written outside the
preserved corpus.

Version 1 accepts communication-only rubrics with explicit `block`/`suggest`
actions and matching canonical annotation IDs; the runtime itself also supports
ordered enforcement and `any_of` rules. Alternate rubrics can be selected with
`--assessment-rubric`, and decider/profile settings with the usual catalog flags.
Rubric-ID mirrors are not independently counted. No annotations are rewritten.
Existing adjudications are not silently substituted for canonical criteria with
different definitions; these labels remain provisional until human validation.
Agreement with them does not establish task calibration or authorize automation.

Additional annotations remain available for future rubric versions, including
`changes_described`, `limitations`, `breaking_changes`, and `change_triviality`.
`description_commit_consistency` additionally requires companion commit metadata,
which the current evidence contract does not ingest. Extend annotations only
where required communication criteria lack matching labels, preserving provenance
and historical reference versions. Change scope can select communication
requirements without asserting code safety; testing explanations do not prove
that tests were actually executed.
