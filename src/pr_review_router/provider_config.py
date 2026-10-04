"""Independent, explicit configuration for local model experiments."""

import ipaddress
import tomllib
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Text

PositiveInt = Annotated[int, Field(gt=0)]


class LocalSettings(Contract):
    """Transport limits shared by local provider roles."""

    endpoint: Text
    model: Text
    timeout_seconds: Annotated[float, Field(gt=0, le=600, allow_inf_nan=False)] = 120.0
    max_response_bytes: Annotated[int, Field(gt=0, le=8_388_608)] = 1_048_576

    @field_validator("endpoint")
    @classmethod
    def local_endpoint(cls, value: str) -> str:
        """Require a loopback server origin without embedded credentials.

        :param value: Configured local server origin.
        :returns: Normalized origin without a trailing slash.
        :raises ValueError: If the URL is not a plain loopback HTTP(S) origin.
        """
        parsed = urlsplit(value)
        hostname = parsed.hostname
        try:
            _ = parsed.port
        except ValueError:
            raise ValueError("endpoint has an invalid port") from None
        try:
            loopback = hostname == "localhost" or ipaddress.ip_address(hostname or "").is_loopback
        except ValueError:
            loopback = False
        if (
            not loopback
            or parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint must be a loopback HTTP(S) origin")
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def explicit_model(cls, value: str) -> str:
        """Reject blank names and obvious hosted-model selectors.

        :param value: Model name supplied by the owner.
        :returns: Explicit local model name.
        :raises ValueError: If the name is blank or selects a cloud model.
        """
        if value != value.strip() or not value.strip() or "cloud" in value.lower():
            raise ValueError("model must name an installed local model")
        return value


class MockSettings(Contract):
    """Select an existing deterministic demonstration provider."""

    backend: Literal["mock"] = "mock"


class DecisionSettings(LocalSettings):
    """Select a local TypeSafe-compatible decision model."""

    backend: Literal["ollaya"] = "ollaya"


class ReviewSettings(LocalSettings):
    """Select one local model with distinct standard and deep prompts."""

    backend: Literal["ollama"] = "ollama"
    context_tokens: PositiveInt = 32768
    standard_max_tokens: PositiveInt = 1024
    deep_max_tokens: PositiveInt = 2048
    temperature: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)] = 0.0
    seed: int = 0
    reasoning_effort: Text | None = "none"

    @model_validator(mode="after")
    def output_fits_context(self) -> "ReviewSettings":
        """Reserve input space at both review depths.

        :returns: Valid settings when output budgets fit the configured context.
        :raises ValueError: If either output budget consumes the context.
        """
        if max(self.standard_max_tokens, self.deep_max_tokens) >= self.context_tokens:
            raise ValueError("review output budgets must be smaller than context_tokens")
        return self


class ProvidersConfig(Contract):
    """Provider selection separate from routing policy and its environment overrides."""

    decision: Annotated[DecisionSettings | MockSettings, Field(discriminator="backend")] = Field(
        default_factory=MockSettings
    )
    review: Annotated[ReviewSettings | MockSettings, Field(discriminator="backend")] = Field(
        default_factory=MockSettings
    )
    shadow_skips: Literal[True] = True


def load_providers_config(path: Path) -> ProvidersConfig:
    """Load explicit provider settings without changing policy loading semantics.

    :param path: TOML provider configuration file.
    :returns: Validated independent role settings.
    :raises OSError: If the file cannot be read.
    :raises ValueError: If TOML or provider settings are invalid.
    """
    return ProvidersConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
