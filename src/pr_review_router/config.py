"""Routing policy loaded from TOML; all fields have offline defaults."""

import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import Field

from .contracts import Contract, Probability


class Policy(Contract):
    skip_confidence: Probability = 0.95
    review_confidence: Probability = 0.85
    max_input_bytes: Annotated[int, Field(gt=0)] = 100_000
    max_files: Annotated[int, Field(gt=0)] = 100
    max_findings: Annotated[int, Field(gt=0)] = 5


def load_policy(path: Path | None) -> Policy:
    if path is None:
        return Policy()
    return Policy.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
