# Held-out scenario review findings

The original 125 development cases also have a
[fresh review under the same criteria](LEGACY-REVIEW.md), with their original
fixtures and historical archive preserved. This page covers the 24 held-out cases.

The extension adds 24 independently authored PRs across twelve new families,
with paired descriptions/reviews and all five reference triviality levels.
The original 125 cases and their historical review archive remain unchanged.
Two additional agents independently labeled all 24 input-only packets blind.
The coordinator compared their answers with provisional author intent. All
participants use the same model family; these are AI judgments, not human
ground truth, calibration, or measured provider quality.

One clear annotation error was corrected: `pr-137`'s proposed retry validation
expected two attempts although the patch changes the ceiling to three. Both
blind reviewers failed the criterion. The initial author label remains in
`authored-reviews.json` and the source case's revision history.

Against the corrected provisional references, all six status criteria agree
in both passes (24 cases per criterion). Native triviality differs despite its
mapped status being `passed` for every classified level:

| Criterion | Cases | Reviewer A mismatches | Reviewer B mismatches | Contested labels |
| --- | ---: | ---: | ---: | ---: |
| title-format | 24 | 0 | 0 | 0 |
| summary-section | 24 | 0 | 0 | 0 |
| change-rationale | 24 | 0 | 0 | 2 |
| description-coverage | 24 | 0 | 0 | 1 |
| review-coverage | 24 | 0 | 0 | 0 |
| testing-explanation | 24 | 0 | 0 | 4 |
| change-triviality (native label) | 24 | 4 | 2 | 10 |

Ordinal confusion is reference score → predicted native label. Every count
below includes uncertain answers; no missing output was discarded.

| Reference level | Cases | Reviewer A predictions | Reviewer B predictions |
| --- | ---: | --- | --- |
| 0 editorial | 4 | editorial: 4 | editorial: 4 |
| 1 mechanical | 4 | mechanical: 4 | mechanical: 2; bounded_behavior: 2 |
| 2 bounded behavior | 8 | bounded_behavior: 4; incompatible: 2; uncertain: 2 | bounded_behavior: 8 |
| 3 substantive | 4 | substantive: 4 | substantive: 4 |
| 4 incompatible | 4 | incompatible: 4 | incompatible: 4 |

The disagreements concentrate in these scenario/level intersections. Remaining
intersections agree in both passes; the generated JSON retains all of them.

| Scenario | Reference level | Support | Reviewer A | Reviewer B |
| --- | --- | ---: | --- | --- |
| complete_account | mechanical | 1 | mechanical | bounded_behavior |
| partial_account | mechanical | 1 | mechanical | bounded_behavior |
| complete_account | bounded_behavior | 2 | bounded_behavior: 1; uncertain: 1 | bounded_behavior: 2 |
| rationale_only | bounded_behavior | 1 | uncertain | bounded_behavior |
| accurate_with_open_tooling_concern | bounded_behavior | 1 | incompatible | bounded_behavior |
| restated_rationale_and_unresolved_review_references | bounded_behavior | 1 | incompatible | bounded_behavior |

These six disputed triviality judgments remain adjudicated **uncertain**:
`pr-128/129` change accessible-name spelling (presentation versus observable
behavior); `pr-130/131` select another lint release without its implementation
or release effects; `pr-144/145` require GNU tar where the previous prerequisites
said tar (local archive output versus host compatibility). The provisional
author scores remain explicit, distinct from adjudications. Do not treat the
aggregate agreement as resolving these cases.

Seventeen criterion labels are contested in total. Fourteen cases contain at
least one contested label; ten contain none. Contested rationale and testing
labels include motivation for Japanese punctuation, requested workflow checks,
and validation that omits a removed Ruby alias. Coverage does not establish
correctness, and an open review concern can still cover every changed file.

Run `uv run python benchmarks/synthetic-prs/legacy-review/review_report.py` to regenerate
`reports/synthetic-prs-label-review.json` from the hash-validated archive.
It contains support and complete confusion by criterion, scenario, reference
triviality level, family, author, split, review confidence (`by_review_confidence`),
and scenario/level intersection. Its grading revision is explicitly `original`
to preserve this author-intent audit while benchmark scoring defaults to latest
adjudications. Its recommendation metrics project the rubric policy onto review
labels; they are not observed router decisions. Human review and broader
external data are still needed before quality assessment or calibration.
