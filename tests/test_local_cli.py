"""Installed CLI integration tests using explicitly synthetic, offline HTTP tapes."""

import json

import pytest
from local_model_helpers import LOCAL
from test_cli import cli

pytestmark = pytest.mark.integration


def test_installed_cli_replays_adapters_and_writes_separate_provenance(tmp_path):
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
        "--output",
        tmp_path / "report.json",
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads((tmp_path / "report.json").read_text())
    artifact = json.loads((tmp_path / "experiment.json").read_text())
    assert report["outcome"] == "reviewed"
    assert report["schema_version"] == "3"
    assert report["decision"]["recommendation"] == "review"
    assert artifact["original_decisions"][0]["recommendation"] == "skip_review"
    assert artifact["mode"] == "replay"
    assert artifact["recording_origin"] == "synthetic"
    assert completed.stdout == ""


def test_corpus_replay_evaluates_reference_metrics_without_inference(tmp_path):
    completed = cli(
        "evaluate",
        "--corpus",
        LOCAL / "corpus.json",
        "--providers-config",
        LOCAL / "replay.toml",
        "--replay-dir",
        LOCAL / "recordings",
        "--output-dir",
        tmp_path / "evaluation",
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["case_count"] == 5
    assert summary["mode"] == "replay"
    assert summary["label_status"] == "provisional"
    assert summary["totals"]["unsafe_skip_recommendations"] == 1
    assert summary["totals"]["missed_planted_defects"] == 0
    assert summary["totals"]["human_handoffs"] == 3
    incomplete = json.loads((tmp_path / "evaluation/incomplete.experiment.json").read_text())
    assert incomplete["http_calls"] == 0
    assert len(list((tmp_path / "evaluation").iterdir())) == 11
    assert summary == json.loads((tmp_path / "evaluation/summary.json").read_text())


def test_replay_mismatch_is_usage_error_and_leaves_output_untouched(tmp_path):
    output = tmp_path / "report.json"
    output.write_text("previous report")
    completed = cli(
        "review",
        "--input",
        LOCAL / "correct-code.json",
        "--providers-config",
        LOCAL / "replay.toml",
        "--replay",
        LOCAL / "recordings/editorial.tape.json",
        "--output",
        output,
        "--experiment-output",
        tmp_path / "experiment.json",
    )
    assert completed.returncode == 2
    assert "refresh recordings explicitly" in completed.stderr
    assert output.read_text() == "previous report"
    assert not (tmp_path / "experiment.json").exists()
    assert completed.stdout == ""


@pytest.mark.parametrize("option", ["--output", "--record", "--experiment-output"])
def test_experiment_outputs_cannot_overwrite_provider_configuration(tmp_path, option):
    provider = tmp_path / "providers.toml"
    provider.write_text((LOCAL / "replay.toml").read_text())
    original = provider.read_text()
    completed = cli(
        "review",
        "--input",
        LOCAL / "editorial.json",
        "--providers-config",
        provider,
        option,
        provider,
    )
    assert completed.returncode == 2
    assert provider.read_text() == original


def test_record_and_replay_are_explicit_and_mutually_exclusive(tmp_path):
    completed = cli(
        "review",
        "--input",
        LOCAL / "editorial.json",
        "--providers-config",
        LOCAL / "replay.toml",
        "--replay",
        LOCAL / "recordings/editorial.tape.json",
        "--record",
        tmp_path / "record.json",
    )
    assert completed.returncode == 2
    assert "mutually exclusive" in completed.stderr
    assert not (tmp_path / "record.json").exists()
    missing_config = cli(
        "review", "--input", LOCAL / "editorial.json", "--record", tmp_path / "record.json"
    )
    assert missing_config.returncode == 2
    assert "require --providers-config" in missing_config.stderr


def test_corpus_rejects_duplicate_ids_and_outside_evidence_before_running(tmp_path):
    corpus = json.loads((LOCAL / "corpus.json").read_text())
    corpus["cases"][1]["case_id"] = corpus["cases"][0]["case_id"]
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    completed = cli(
        "evaluate",
        "--corpus",
        path,
        "--providers-config",
        LOCAL / "replay.toml",
        "--output-dir",
        tmp_path / "outputs",
    )
    assert completed.returncode == 2
    assert not (tmp_path / "outputs").exists()
    corpus["cases"] = [corpus["cases"][0]]
    corpus["cases"][0]["evidence"] = "../outside.json"
    path.write_text(json.dumps(corpus))
    completed = cli(
        "evaluate",
        "--corpus",
        path,
        "--providers-config",
        LOCAL / "replay.toml",
        "--output-dir",
        tmp_path / "outputs",
    )
    assert completed.returncode == 2
    assert not (tmp_path / "outputs").exists()


def test_invalid_provider_config_and_direct_policy_are_usage_errors(tmp_path):
    config = tmp_path / "providers.toml"
    config.write_text(
        '[review]\nbackend="ollama"\nmodel="local"\nendpoint="http://secret@localhost"\n'
    )
    completed = cli("review", "--input", LOCAL / "editorial.json", "--providers-config", config)
    assert completed.returncode == 2
    assert "secret" not in completed.stderr
    policy = tmp_path / "policy.toml"
    policy.write_text("allow_direct_rejection = true\n")
    completed = cli(
        "review",
        "--input",
        LOCAL / "editorial.json",
        "--providers-config",
        LOCAL / "replay.toml",
        "--config",
        policy,
    )
    assert completed.returncode == 2
    assert "direct acceptance and rejection" in completed.stderr
