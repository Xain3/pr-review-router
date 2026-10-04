"""Routing policy loaded from TOML; all fields have offline defaults."""

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Probability, Text

Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class RubricCriterion(Contract):
    criterion_id: Text
    description: Text
    field: Literal["title", "body"]
    check: Literal["non_empty", "min_words", "contains", "section_nonempty"]
    value: Text | None = None
    minimum_words: Annotated[int, Field(gt=0)] | None = None
    weight: Annotated[int, Field(gt=0)] = 1
    blocker: Literal["hard", "soft"] = "soft"
    pass_score: Percent = 100

    @model_validator(mode="after")
    def check_arguments(self) -> "RubricCriterion":
        if self.check == "min_words":
            if self.minimum_words is None or self.value is not None:
                raise ValueError("min_words requires minimum_words and forbids value")
        elif self.check in {"contains", "section_nonempty"}:
            if self.value is None or self.minimum_words is not None:
                raise ValueError(f"{self.check} requires value and forbids minimum_words")
        elif self.value is not None or self.minimum_words is not None:
            raise ValueError("non_empty does not accept value or minimum_words")
        if self.check == "section_nonempty" and self.field != "body":
            raise ValueError("section_nonempty criteria must use field = 'body'")
        return self


class Policy(Contract):
    allow_direct_acceptance: bool = False
    allow_direct_rejection: bool = False
    acceptance_confidence: Probability = 0.95
    rejection_confidence: Probability = 0.95
    review_behavior: Literal["escalate", "feedback"] = "escalate"
    feedback_depth: Literal["standard", "deep"] = "standard"
    acceptance_feedback: bool = False
    unresolved_outcome: Literal["needs_human_review", "rejected"] = "needs_human_review"
    skip_confidence: Probability = 0.95
    review_confidence: Probability = 0.85
    max_input_bytes: Annotated[int, Field(gt=0)] = 100_000
    max_files: Annotated[int, Field(gt=0)] = 100
    max_findings: Annotated[int, Field(gt=0)] = 5
    title_format: Literal["any", "conventional_commit"] = "any"
    required_body_sections: list[Text] = Field(default_factory=list)
    rubric_minimum_score: Percent = 70
    rubric_criteria: list[RubricCriterion] = Field(default_factory=list)

    @field_validator("rubric_criteria")
    @classmethod
    def unique_rubric_criteria(cls, criteria: list[RubricCriterion]) -> list[RubricCriterion]:
        ids = [criterion.criterion_id for criterion in criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("rubric criterion IDs must be unique")
        return criteria

    @field_validator("required_body_sections")
    @classmethod
    def validate_body_sections(cls, sections: list[str]) -> list[str]:
        if any(
            not section.strip() or section != section.strip() or "\n" in section or "\r" in section
            for section in sections
        ):
            raise ValueError("body section names must be nonempty single-line text")
        if len(sections) != len(set(sections)):
            raise ValueError("body section names must be unique")
        return sections


def load_policy(path: Path | None) -> Policy:
    if path is None:
        return Policy()
    return Policy.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
