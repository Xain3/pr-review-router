"""Named model variants and reusable role selections, separate from routing policy."""

import tomllib
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, model_validator

from .contracts import Contract, Text
from .provider_config import (
    DecisionSettings,
    JevSettings,
    MockSettings,
    ProvidersConfig,
    ReviewSettings,
)


class ModelSelection(Contract):
    """Names selected for the independent decision and review roles."""

    decider: Text = "mock"
    reviewer: Text = "mock"


class ModelProfile(Contract):
    """Partial role selection layered over catalog defaults."""

    decider: Text | None = None
    reviewer: Text | None = None


class ModelCatalog(Contract):
    """Validated named provider variants, defaults, and optional profiles."""

    models: dict[
        str,
        Annotated[
            DecisionSettings | JevSettings | ReviewSettings | MockSettings,
            Field(discriminator="backend"),
        ],
    ]
    defaults: ModelSelection = Field(default_factory=ModelSelection)
    profiles: dict[str, ModelProfile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_selections(self) -> "ModelCatalog":
        """Validate every default and profile before any provider can run.

        :returns: Catalog with valid names and role capabilities.
        :raises ValueError: If a selected name is absent or incompatible with its role.
        """
        self.resolve()
        for name in self.profiles:
            self.resolve(profile=name)
        return self

    def resolve(
        self,
        *,
        profile: str | None = None,
        decider: str | None = None,
        reviewer: str | None = None,
        overrides: list[str] | None = None,
    ) -> ProvidersConfig:
        """Apply profile, role selections, then typed TOML setting overrides.

        :param profile: Optional named partial selection.
        :param decider: Optional decision-model name overriding the profile.
        :param reviewer: Optional review-model name overriding the profile.
        :param overrides: Repeated ``decision.FIELD=VALUE`` or ``review.FIELD=VALUE``.
        :returns: Fully validated settings consumed by existing provider adapters.
        :raises ValueError: If names, role capabilities, or overrides are invalid.
        """
        names = self.defaults.model_dump()
        if profile is not None:
            if profile not in self.profiles:
                raise ValueError("unknown model profile")
            names.update(self.profiles[profile].model_dump(exclude_none=True))
        names.update(
            {
                key: value
                for key, value in (("decider", decider), ("reviewer", reviewer))
                if value is not None
            }
        )
        data: dict[str, Any] = {}
        for selection, role in (("decider", "decision"), ("reviewer", "review")):
            if names[selection] not in self.models:
                raise ValueError("unknown model name")
            data[role] = self.models[names[selection]].model_dump()
        # Validate capabilities before overrides: overrides cannot change model identity.
        ProvidersConfig.model_validate(data)
        seen = set()
        for override in overrides or []:
            key, separator, raw = override.partition("=")
            role, dot, field = key.partition(".")
            if (
                not separator
                or not dot
                or role not in data
                or field not in data[role]
                or field in {"backend", "model"}
                or key in seen
            ):
                raise ValueError("invalid or duplicate model override")
            seen.add(key)
            data[role][field] = tomllib.loads(f"value = {raw}")["value"]
        return ProvidersConfig.model_validate(data)


def resolve_catalog_path(path: Path | None = None) -> Path:
    """Find an explicit catalog, working-directory catalog, or packaged fallback.

    :param path: Explicit catalog path, which must exist when loaded.
    :returns: Selected catalog path without searching parent directories.
    """
    if path is not None:
        return path
    local = Path("models.toml")
    return local if local.exists() else Path(__file__).with_name("models.toml")


def load_model_catalog(path: Path) -> ModelCatalog:
    """Read a strict UTF-8 TOML catalog.

    :param path: Catalog document to load.
    :returns: Validated catalog including unused variants and profiles.
    :raises OSError: If the catalog cannot be read.
    :raises ValueError: If TOML or catalog settings are invalid.
    """
    return ModelCatalog.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
