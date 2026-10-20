# Synthetic PR benchmarks

Start with the [communication benchmark](communication/README.md) for the current
PR title/description check. The older review-routing experiment is maintained
separately under [legacy-review/](legacy-review/README.md).

| Location | Ownership and purpose |
| --- | --- |
| `communication/` | `communication-v1` runner and rubric for `assess`; no reviewer, supplied-review requirement, or legacy routing policy. Model selection uses the normal model catalog. |
| `legacy-review/` | Legacy router runner, review-label report, triviality scorer, active and predecessor rubrics, pinned routing policy, and live provider settings. |
| `prs/`, `index.json`, `INDEX.md` | Shared evidence, companion metadata, provisional annotations, and case index. Optional `review.md` files are legacy inputs only. |
| `splits.json`, `authored/` | Shared family partitions and independently authored source cases. |
| `independent-review/` | Preserved legacy review adjudications, protocols, registry, and historical snapshots. Communication-v1 uses canonical author communication labels instead. |
| `corpus*.json` | Generated legacy `evaluate` manifests, kept beside the shared cases so evidence paths stay within the corpus root. Communication does not load these manifests. |
| `generate.py`, `validate.py` | Shared corpus generation and integrity checks, including legacy compatibility artifacts. |
| [CORPUS.md](CORPUS.md) | Detailed corpus, annotation, provenance, and maintenance guide. |

The 157 cases, identities, family splits, annotations, and reference history are
preserved. Reference labels stay out of model inputs and remain provisional until
human validation. All reports are advisory and runtime output belongs in
`reports/`.

Run from the repository root:

```sh
# Current communication check, deterministic and offline by default.
uv run python benchmarks/synthetic-prs/communication/benchmark.py --split evaluation

# Validate the complete shared corpus and legacy reference history offline.
uv run python benchmarks/synthetic-prs/validate.py

# Inspect legacy reference revisions without invoking live inference.
uv run python benchmarks/synthetic-prs/legacy-review/benchmark.py --list-reference-revisions
```

The pinned `legacy-review/providers.toml` selects live local inference for the
legacy runner; it is not used by the communication benchmark. See each runner's
guide for recording, replay, and scoring commands.
