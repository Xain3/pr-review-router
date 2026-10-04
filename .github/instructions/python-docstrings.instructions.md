---
name: Python Docstrings
description: "Use when writing or editing Python code; describes this project's reStructuredText docstring convention for Pylance documentation."
applyTo: "**/*.py"
---
# Python Docstrings

The canonical repository guidance is in [`AGENTS.md`](../../AGENTS.md#python-docstrings).
Follow that section when editing Python files.

- Start callable docstrings with a concise summary, then use reStructuredText fields for details: `:param name:`, `:returns:`, and `:raises ExceptionType:` as applicable.
- Omit `self` and `cls` from parameter fields. Keep types in Python annotations instead of duplicating them with `:type:` or `:rtype:` fields.
- Prefer reStructuredText fields over Google- or NumPy-style sections so Pylance can surface parameter descriptions in function hovers and signature help.
