"""Routing policy loaded from TOML; all fields have offline defaults."""

import json
import os
import tomllib
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Probability, Text

Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
ENV_PREFIX = "PR_REVIEW_ROUTER_"
CONFIG_ENV_VAR = f"{ENV_PREFIX}CONFIG"

# Hardcoded fallbacks used when settings are omitted from the TOML configuration.
FALLBACK_RUBRIC_PASS_SCORE = 100
FALLBACK_RUBRIC_WEIGHT = 1
FALLBACK_RUBRIC_BLOCKER = "soft"

FALLBACK_POLICY_ALLOW_DIRECT_ACCEPTANCE = False
FALLBACK_POLICY_ALLOW_DIRECT_REJECTION = False
FALLBACK_POLICY_ACCEPTANCE_CONFIDENCE = 0.95
FALLBACK_POLICY_REJECTION_CONFIDENCE = 0.95
FALLBACK_POLICY_REVIEW_BEHAVIOR = "escalate"
FALLBACK_POLICY_FEEDBACK_DEPTH = "standard"
FALLBACK_POLICY_ACCEPTANCE_FEEDBACK = False
FALLBACK_POLICY_UNRESOLVED_OUTCOME = "needs_human_review"
FALLBACK_POLICY_SKIP_CONFIDENCE = 0.95
FALLBACK_POLICY_REVIEW_CONFIDENCE = 0.85
FALLBACK_POLICY_MAX_INPUT_BYTES = 100_000
FALLBACK_POLICY_MAX_FILES = 100
FALLBACK_POLICY_MAX_FINDINGS = 5
FALLBACK_POLICY_TITLE_FORMAT = "any"
FALLBACK_POLICY_RUBRIC_MINIMUM_SCORE = 70


class RubricCriterion(Contract):
    """A weighted check applied to the pull request title or body."""

    criterion_id: Text
    description: Text
    field: Literal["title", "body"]
    check: Literal["non_empty", "min_words", "contains", "section_nonempty"]
    value: Text | None = None
    minimum_words: Annotated[int, Field(gt=0)] | None = None
    weight: Annotated[int, Field(gt=0)] = FALLBACK_RUBRIC_WEIGHT
    blocker: Literal["hard", "soft"] = FALLBACK_RUBRIC_BLOCKER
    pass_score: Percent = FALLBACK_RUBRIC_PASS_SCORE

    @model_validator(mode="after")
    def check_arguments(self) -> "RubricCriterion":
        """Ensure each check receives only the arguments it understands.

        :returns: This criterion after successful validation.
        :raises ValueError: If the check's configured arguments are inconsistent.
        """
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
    """Validated routing thresholds, limits, text checks, and rubric settings."""

    allow_direct_acceptance: bool = FALLBACK_POLICY_ALLOW_DIRECT_ACCEPTANCE
    allow_direct_rejection: bool = FALLBACK_POLICY_ALLOW_DIRECT_REJECTION
    acceptance_confidence: Probability = FALLBACK_POLICY_ACCEPTANCE_CONFIDENCE
    rejection_confidence: Probability = FALLBACK_POLICY_REJECTION_CONFIDENCE
    review_behavior: Literal["escalate", "feedback"] = FALLBACK_POLICY_REVIEW_BEHAVIOR
    feedback_depth: Literal["standard", "deep"] = FALLBACK_POLICY_FEEDBACK_DEPTH
    acceptance_feedback: bool = FALLBACK_POLICY_ACCEPTANCE_FEEDBACK
    unresolved_outcome: Literal["needs_human_review", "rejected"] = (
        FALLBACK_POLICY_UNRESOLVED_OUTCOME
    )
    skip_confidence: Probability = FALLBACK_POLICY_SKIP_CONFIDENCE
    review_confidence: Probability = FALLBACK_POLICY_REVIEW_CONFIDENCE
    max_input_bytes: Annotated[int, Field(gt=0)] = FALLBACK_POLICY_MAX_INPUT_BYTES
    max_files: Annotated[int, Field(gt=0)] = FALLBACK_POLICY_MAX_FILES
    max_findings: Annotated[int, Field(gt=0)] = FALLBACK_POLICY_MAX_FINDINGS
    title_format: Literal["any", "conventional_commit"] = FALLBACK_POLICY_TITLE_FORMAT
    required_body_sections: list[Text] = Field(default_factory=list)
    rubric_minimum_score: Percent = FALLBACK_POLICY_RUBRIC_MINIMUM_SCORE
    rubric_criteria: list[RubricCriterion] = Field(default_factory=list)

    @field_validator("rubric_criteria")
    @classmethod
    def unique_rubric_criteria(cls, criteria: list[RubricCriterion]) -> list[RubricCriterion]:
        """Keep criterion IDs unique for unambiguous result reporting.

        :param criteria: Configured rubric criteria to validate.
        :returns: The criteria when all IDs are unique.
        :raises ValueError: If any criterion ID appears more than once.
        """
        ids = [criterion.criterion_id for criterion in criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("rubric criterion IDs must be unique")
        return criteria

    @field_validator("required_body_sections")
    @classmethod
    def validate_body_sections(cls, sections: list[str]) -> list[str]:
        """Require unique, nonempty single-line section names.

        :param sections: Required pull request body section names.
        :returns: The section names when they are valid and unique.
        :raises ValueError: If a name is empty, multiline, untrimmed, or duplicated.
        """
        if any(
            not section.strip() or section != section.strip() or "\n" in section or "\r" in section
            for section in sections
        ):
            raise ValueError("body section names must be nonempty single-line text")
        if len(sections) != len(set(sections)):
            raise ValueError("body section names must be unique")
        return sections


def resolve_config_path(path: Path | None, environ: Mapping[str, str] | None = None) -> Path | None:
    """Choose an explicit policy path before the environment-selected path.

    :param path: Explicit policy path, if supplied.
    :param environ: Optional environment mapping used when no path is explicit.
    :returns: Selected policy path, or ``None`` when no path is configured.
    :raises ValueError: If the configuration environment variable is empty.
    """
    if path is not None:
        return path
    environment = os.environ if environ is None else environ
    configured_path = environment.get(CONFIG_ENV_VAR)
    if configured_path is None:
        return None
    if not configured_path:
        raise ValueError(f"{CONFIG_ENV_VAR} must not be empty")
    return Path(configured_path)


def load_policy(
    path: Path | None,
    environ: Mapping[str, str] | None = None,
) -> Policy:
    """Load packaged defaults or a TOML policy, then apply per-setting overrides.

    :param path: Optional TOML policy path; ``None`` selects packaged defaults.
    :param environ: Optional environment mapping for per-setting overrides.
    :returns: Validated routing policy.
    :raises OSError: If a policy file cannot be read.
    :raises tomllib.TOMLDecodeError: If the policy contains invalid TOML.
    :raises pydantic.ValidationError: If policy values fail validation.
    """
    if path is None:
        contents = files("pr_review_router").joinpath("defaults.toml").read_text(encoding="utf-8")
    else:
        contents = path.read_text(encoding="utf-8")
    values = tomllib.loads(contents)
    environment = os.environ if environ is None else environ
    for field_name in Policy.model_fields:
        variable = f"{ENV_PREFIX}{field_name.upper()}"
        if variable not in environment:
            continue
        raw_value = environment[variable]
        try:
            values[field_name] = json.loads(raw_value)
        except json.JSONDecodeError:
            values[field_name] = raw_value
    return Policy.model_validate(values)
