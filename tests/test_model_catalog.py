"""Offline catalog precedence, validation, and CLI compatibility checks."""

import json

import pytest
from local_model_helpers import LOCAL
from test_cli import ROOT, cli

from pr_review_router.model_catalog import load_model_catalog, resolve_catalog_path
from pr_review_router.provider_config import load_providers_config

pytestmark = pytest.mark.unit


def test_profile_role_and_setting_precedence():
    catalog = load_model_catalog(ROOT / "models.toml")
    assert catalog.resolve().decision.backend == "mock"
    config = catalog.resolve(
        profile="thorough",
        reviewer="local",
        overrides=['review.reasoning_effort="low"', "review.deep_max_tokens=3072"],
    )
    assert config.decision.backend == "ollaya"
    assert config.review.backend == "ollama"
    assert config.review.reasoning_effort == "low"
    assert config.review.standard_max_tokens == 1024
    assert config.review.deep_max_tokens == 3072
    assert catalog.resolve(profile="thorough").review.model_dump()["reasoning_effort"] == "high"


@pytest.mark.parametrize(
    "selection",
    [
        {"profile": "absent"},
        {"decider": "absent"},
        {"decider": "local"},
        {"reviewer": "winnow"},
        {"overrides": ["review.temperature=3"]},
        {"overrides": ['review.backend="mock"']},
        {"overrides": ['review.model="other"']},
        {"overrides": ["review.seed=1", "review.seed=2"]},
        {"overrides": ["review.unknown=1"]},
        {"overrides": ["review.seed=invalid"]},
        {"overrides": ["review.context_tokens=100"]},
    ],
)
def test_invalid_selection_fails_before_provider_execution(selection):
    catalog = load_model_catalog(ROOT / "models.toml")
    with pytest.raises(ValueError):
        catalog.resolve(**({"profile": "local"} | selection))


def test_catalog_discovery_and_packaged_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    packaged = resolve_catalog_path()
    assert packaged.is_file()
    assert packaged.read_bytes() == (ROOT / "models.toml").read_bytes()
    local = tmp_path / "models.toml"
    local.write_text('[models.mock]\nbackend="mock"\n')
    assert resolve_catalog_path().resolve() == local
    explicit = tmp_path / "missing.toml"
    assert resolve_catalog_path(explicit) == explicit
    with pytest.raises(OSError):
        load_model_catalog(explicit)
    local.write_text('[models.mock]\nbackend="mock"\n[profiles.broken]\nreviewer="missing"\n')
    with pytest.raises(ValueError):
        load_model_catalog(local)


def test_catalog_cli_replays_identical_resolved_legacy_settings(tmp_path):
    legacy = load_providers_config(LOCAL / "replay.toml")
    # Serialize the sanitized legacy fixture under catalog names without changing settings.
    text = (LOCAL / "replay.toml").read_text().replace("shadow_skips = true", "")
    text = text.replace("[decision]", "[models.decider]").replace("[review]", "[models.reviewer]")
    text += '\n[profiles.replay]\ndecider="decider"\nreviewer="reviewer"\n'
    catalog = tmp_path / "models.toml"
    catalog.write_text('[models.mock]\nbackend="mock"\n' + text)
    assert load_model_catalog(catalog).resolve(profile="replay") == legacy
    artifact = tmp_path / "experiment.json"
    completed = cli(
        "review",
        "--input",
        LOCAL / "editorial.json",
        "--models-config",
        catalog,
        "--profile",
        "replay",
        "--decider",
        "decider",
        "--reviewer",
        "reviewer",
        "--model-override",
        f"review.seed={legacy.review.model_dump()['seed']}",
        "--replay",
        LOCAL / "recordings/editorial.tape.json",
        "--experiment-output",
        artifact,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(artifact.read_text())["mode"] == "replay"
    evaluated = cli(
        "evaluate",
        "--corpus",
        LOCAL / "corpus.json",
        "--models-config",
        catalog,
        "--profile",
        "replay",
        "--replay-dir",
        LOCAL / "recordings",
        "--output-dir",
        tmp_path / "evaluation",
    )
    assert evaluated.returncode == 0, evaluated.stderr
    assert json.loads(evaluated.stdout)["case_count"] == 5


@pytest.mark.parametrize(
    "option,value",
    [
        ("--profile", "local"),
        ("--decider", "mock"),
        ("--reviewer", "mock"),
        ("--models-config", "missing.toml"),
        ("--model-override", "review.seed=1"),
    ],
)
def test_explicit_legacy_configuration_rejects_catalog_options(option, value):
    completed = cli(
        "review",
        "--input",
        LOCAL / "editorial.json",
        "--providers-config",
        LOCAL / "replay.toml",
        option,
        value,
    )
    assert completed.returncode == 2
    assert "cannot be combined" in completed.stderr


def test_catalog_input_cannot_be_overwritten(tmp_path):
    catalog = tmp_path / "models.toml"
    original = '[models.mock]\nbackend="mock"\n'
    catalog.write_text(original)
    completed = cli(
        "review",
        "--input",
        LOCAL / "editorial.json",
        "--models-config",
        catalog,
        "--output",
        catalog,
    )
    assert completed.returncode == 2
    assert catalog.read_text() == original


def test_partial_profile_inherits_custom_defaults(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[defaults]\ndecider="custom"\nreviewer="custom"\n'
        '[models.custom]\nbackend="mock"\n'
        '[models.other]\nbackend="mock"\n'
        '[profiles.partial]\nreviewer="other"\n'
    )
    catalog = load_model_catalog(path)
    assert catalog.resolve(profile="partial").decision.backend == "mock"
    assert catalog.resolve(profile="partial").review.backend == "mock"


def test_legacy_bypasses_invalid_working_directory_catalog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models.toml").write_text("invalid TOML")
    completed = cli(
        "review",
        "--input",
        LOCAL / "editorial.json",
        "--providers-config",
        LOCAL / "replay.toml",
        "--replay",
        LOCAL / "recordings/editorial.tape.json",
        "--experiment-output",
        tmp_path / "experiment.json",
    )
    assert completed.returncode == 0, completed.stderr
    invalid = cli("review", "--input", LOCAL / "editorial.json")
    assert invalid.returncode == 2
    assert "model catalog" in invalid.stderr


def test_live_catalog_defaults_keep_preflight_offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = (ROOT / "models.toml").read_text().replace('decider = "mock"', 'decider = "winnow"')
    (tmp_path / "models.toml").write_text(text)
    completed = cli(
        "review",
        "--input",
        LOCAL / "incomplete.json",
        "--experiment-output",
        tmp_path / "experiment.json",
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["outcome"] == "needs_human_review"
    artifact = json.loads((tmp_path / "experiment.json").read_text())
    assert artifact["http_calls"] == 0
    assert artifact["providers"]["decision"]["backend"] == "ollaya"
