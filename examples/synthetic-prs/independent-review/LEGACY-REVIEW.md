# Fresh review of the original 125 PRs

All original development cases were reviewed again using the same unchanged
seven-criterion rubric and evidence-based arbitration used for the 24 new cases.
Three fresh agents received opaque input-only packets with no conversation
history, author labels, scenario identities, prior reviews, or other reviewers'
answers. Pass A divided 63/62 cases between two agents; pass B independently
reviewed all 125 in a different order. Each case therefore has two independent
judgments per criterion: 875 criterion pairs, or 1,750 raw judgments.

[`legacy-reviews.json`](legacy-reviews.json) stores both full judgments,
evidence pointers, reservations, final adjudications, and the earlier decision.
The original PR inputs, provisional author annotations, family partitions, and
historical [`reviews.json`](reviews.json) remain unchanged. Hashes bind the new
review to its evidence, rubric, and preserved historical archive. All participants
use the same model family; this is AI review, not human ground truth,
calibration, code execution, or a measured live provider-quality evaluation.

## Arbitration findings

The coordinating adjudicator changed three prior judgments because the evidence
and rubric settle them. Both fresh reviewers independently supported each change:

| Case / criterion | Author reference and prior adjudication | Current adjudication | Evidence |
| --- | --- | --- | --- |
| pr-087 / testing-explanation | failed | passed | Summary explicitly names matching and mismatching ASCII-token tests, and the added test patch contains those checks. This criterion does not require a separate Testing heading. |
| pr-120 / description-coverage | passed | failed | Summary identifies the source and test changes, but Limitations says only `src/pager.py` is edited despite an added `tests/test_pager.py`. Accuracy applies to the whole description. |
| pr-121 / description-coverage | passed | failed | The same explicit source-only scope claim contradicts the added test patch, despite the accurate Summary. |

These decisions remain contested where a narrower reading of “edited” or a
stricter interpretation of testing prose is plausible. The decision and its
evidence are visible; the old author labels have not been silently replaced.

Three patch families remain **uncertain**, consistently across all their variants:

| Family | Cases | Competing classifications | Missing or conflicting basis |
| --- | ---: | --- | --- |
| cli-help | 11 | mechanical / bounded_behavior | The rubric permits presentation edits as mechanical but also assigns output-value changes to bounded behavior; the exported string changes its displayed value. |
| cache-key | 12 | bounded_behavior / incompatible | Calling `key.lower()` changes lookup behavior and can reject previously accepted key types. The patches do not establish which types and normalization rules were supported. |
| token-compare | 12 | substantive / incompatible | Replacing equality with `compare_digest` is security-sensitive and can narrow accepted token types. The prior supported ASCII/type contract is absent from patch evidence. |

The two reviewers differ locally on 20 triviality labels. For the other 15
variants in these same families, both local judgments match the author reference,
but the missing contract or overlapping definition is unchanged. Family-level
arbitration therefore retains uncertainty for all 35: identical diffs cannot
receive different final impact classifications because descriptions or reviewer
assignments differ. Both raw judgments remain unchanged. This is an explicit
coordinator decision, rather than a claim of reviewer consensus.

The fresh archive has **49 contested criterion labels across 45 cases**.
Thirty-eight current judgments differ from the author reference: the three
evidence-supported changes and 35 unresolved triviality classifications. The
remaining 80 cases have no contested criterion in these passes; this does not
confer human validation.

## Confusion against provisional author references

Counts below compare each fresh reviewer with the unchanged author labels.
Disagreement can expose a reference-label error or unresolved ambiguity; it is
not automatically a model error. The corrected adjudications are separate.

| Criterion | Support | Pass A mismatches | Pass B mismatches | Contested labels after arbitration |
| --- | ---: | ---: | ---: | ---: |
| title-format | 125 | 0 | 0 | 0 |
| summary-section | 125 | 0 | 0 | 0 |
| change-rationale | 125 | 0 | 0 | 6 |
| description-coverage | 125 | 2 | 2 | 3 |
| review-coverage | 125 | 0 | 0 | 0 |
| testing-explanation | 125 | 1 | 1 | 4 |
| change-triviality (native label) | 125 | 20 | 0 | 36 |

Mapped triviality status is `passed` for all 125 classifications in both passes,
which hides the native-level differences. The ordinal confusion is:

| Author reference level | Support | Pass A predictions | Pass B predictions |
| --- | ---: | --- | --- |
| 0 editorial | 23 | editorial: 23 | editorial: 23 |
| 1 mechanical | 13 | mechanical: 2; bounded_behavior: 11 | mechanical: 13 |
| 2 bounded behavior | 38 | bounded_behavior: 34; incompatible: 4 | bounded_behavior: 38 |
| 3 substantive | 26 | substantive: 21; incompatible: 5 | substantive: 26 |
| 4 incompatible | 25 | incompatible: 25 | incompatible: 25 |

Scenario confusion also matters. These are native-level mismatches against author
intent, including descriptions that deliberately misstate the diff:

| Scenario | Support | Pass A native mismatches | Pass B native mismatches |
| --- | ---: | ---: | ---: |
| good | 10 | 2 | 0 |
| nonconventional_title | 10 | 1 | 0 |
| gibberish_title | 10 | 2 | 0 |
| missing_rationale | 10 | 2 | 0 |
| missing_change_description | 10 | 1 | 0 |
| title_description_mismatch | 10 | 2 | 0 |
| commit_description_mismatch | 10 | 2 | 0 |
| diff_description_mismatch | 10 | 3 | 0 |
| additional_cases | 10 | 2 | 0 |
| combined_failures | 10 | 2 | 0 |
| what_not_why | 10 | 1 | 0 |
| omitted_change | 7 | 0 | 0 |
| boundary | 8 | 0 | 0 |

In the `boundary` scenario, native triviality agrees while description coverage
disagrees on pr-120 (reference level 1) and pr-121 (level 2). In
`additional_cases`, testing explanation disagrees on pr-087 (level 3). These
criterion/scenario/level intersections expose the three reference issues even
though overall status agreement is high. Pass B's perfect native agreement with
author intent does not resolve the 35 cases left uncertain by arbitration.

Run `uv run python examples/synthetic-prs/review_report.py --split development`
to write the complete report to `reports/synthetic-prs-label-review-development.json`.
Use `--split all` for the combined 149-case report; evaluation remains the default.
Every group retains support, criterion confusion, native-level confusion, and
the distinction between contested and uncontested AI reviews in
`by_review_confidence`. The audit explicitly grades against `original`; benchmark
scoring defaults to the latest adjudications. Report version 2
also lists final adjudication overrides and unresolved disagreements separately
from agreement with author intent. The combined archive retains 41 unresolved
triviality judgments: 35 original cases and six held-out cases.
