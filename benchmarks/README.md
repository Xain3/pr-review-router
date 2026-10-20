# Benchmarks

This directory contains benchmark datasets, annotations, evaluation runners, and
validation tools. These assets are kept separate from the runnable product
examples in [`examples/`](../examples/README.md).

## Synthetic PR corpus

The 157-case synthetic corpus supports the legacy router rubric benchmark and a
separately versioned PR communication benchmark. Its guide covers the corpus,
partitions, labels, runners, and offline validation:

- [Corpus and benchmark guide](synthetic-prs/README.md)
- [Human-readable case index](synthetic-prs/INDEX.md)

Validate the corpus from the repository root with:

```sh
uv run python benchmarks/synthetic-prs/validate.py
```
