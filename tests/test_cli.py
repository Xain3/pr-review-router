import json
import os
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

from pr_review_router import cli as cli_module

ROOT = Path(__file__).resolve().parents[1]


def cli(*arguments, environment_overrides=None):
    # Exercise the actual installed console script, with all model credentials removed.
    executable = shutil.which("pr-review-router")
    if executable is None:
        pytest.fail("pr-review-router console script is not installed")

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENAI_API_KEY", "TYPESAFE_API_KEY", "REVIEW_MODEL_API_KEY"}
        and not key.startswith("PR_REVIEW_ROUTER_")
    }
    environment.update(environment_overrides or {})
    return subprocess.run(
        [executable, *map(str, arguments)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


@pytest.mark.smoke
def test_help_and_version():
    help_result = cli("--help")
    assert help_result.returncode == 0
    assert "review" in help_result.stdout
    version_result = cli("--version")
    assert version_result.returncode == 0
    assert version_result.stdout.strip() == f"pr-review-router {version('pr-review-router')}"


@pytest.mark.unit
def test_version_without_package_metadata(monkeypatch):
    def missing_distribution(_name):
        _ = _name
        raise PackageNotFoundError

    monkeypatch.setattr(cli_module, "version", missing_distribution)
    assert cli_module._get_version() == "unknown"


@pytest.mark.smoke
def test_stdout_review_without_credentials():
    completed = cli("review", "--input", ROOT / "examples/diff/evidence.json")
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["outcome"] == "skipped"
    assert report["advisory_only"] is True
    assert completed.stderr == ""


@pytest.mark.integration
def test_policy_and_private_output_file(tmp_path):
    policy = tmp_path / "policy.toml"
    policy.write_text("skip_confidence = 1.0\n", encoding="utf-8")
    output = tmp_path / "reports" / "report.json"
    completed = cli(
        "review",
        "--input",
        ROOT / "examples/diff/evidence.json",
        "--config",
        policy,
        "--output",
        output,
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["outcome"] == "reviewed"
    assert report["route"] == "standard"
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.integration
def test_config_path_environment_and_cli_precedence(tmp_path):
    environment_policy = tmp_path / "environment.toml"
    environment_policy.write_text("skip_confidence = 1.0\n", encoding="utf-8")
    cli_policy = tmp_path / "cli.toml"
    cli_policy.write_text("skip_confidence = 0.95\n", encoding="utf-8")

    from_environment = cli(
        "review",
        "--input",
        ROOT / "examples/diff/evidence.json",
        environment_overrides={"PR_REVIEW_ROUTER_CONFIG": str(environment_policy)},
    )
    assert from_environment.returncode == 0, from_environment.stderr
    assert json.loads(from_environment.stdout)["outcome"] == "reviewed"

    from_cli = cli(
        "review",
        "--input",
        ROOT / "examples/diff/evidence.json",
        "--config",
        cli_policy,
        environment_overrides={"PR_REVIEW_ROUTER_CONFIG": str(environment_policy)},
    )
    assert from_cli.returncode == 0, from_cli.stderr
    assert json.loads(from_cli.stdout)["outcome"] == "skipped"


@pytest.mark.integration
def test_environment_policy_override_and_invalid_value(tmp_path):
    policy = tmp_path / "policy.toml"
    policy.write_text("skip_confidence = 1.0\n", encoding="utf-8")
    completed = cli(
        "review",
        "--input",
        ROOT / "examples/diff/evidence.json",
        "--config",
        policy,
        environment_overrides={"PR_REVIEW_ROUTER_SKIP_CONFIDENCE": "0.95"},
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["outcome"] == "skipped"

    invalid = cli(
        "review",
        "--input",
        ROOT / "examples/diff/evidence.json",
        environment_overrides={"PR_REVIEW_ROUTER_MAX_FILES": "0"},
    )
    assert invalid.returncode == 2
    assert invalid.stdout == ""
    assert "max_files" in invalid.stderr


@pytest.mark.integration
def test_human_handoff_is_an_advisory_success():
    completed = cli("review", "--input", ROOT / "examples/diff/concern.json")
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["outcome"] == "needs_human_review"
    assert report["findings"][0]["title"] == "Synthetic mock concern"


@pytest.mark.parametrize(
    "content",
    ["{", '{"PRIVATE_EVIDENCE":"secret"}', '{"number":true}', "null", "[]"],
)
@pytest.mark.integration
def test_invalid_evidence_fails_without_output_or_input_leak(tmp_path, content):
    input_path = tmp_path / "input.json"
    input_path.write_text(content, encoding="utf-8")
    output = tmp_path / "report.json"
    output.write_text("previous report", encoding="utf-8")
    completed = cli("review", "--input", input_path, "--output", output)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "secret" not in completed.stderr
    assert output.read_text(encoding="utf-8") == "previous report"


@pytest.mark.parametrize(
    "content",
    ["max_findings = 0", "max_files = true", "unknown = 1", "skip_confidence = 2.0", "[broken"],
)
@pytest.mark.integration
def test_invalid_policy_is_a_usage_error(tmp_path, content):
    policy = tmp_path / "policy.toml"
    policy.write_text(content, encoding="utf-8")
    completed = cli("review", "--input", ROOT / "examples/diff/evidence.json", "--config", policy)
    assert completed.returncode == 2
    assert completed.stdout == ""


@pytest.mark.integration
def test_missing_file_is_a_usage_error(tmp_path):
    completed = cli("review", "--input", tmp_path / "missing.json")
    assert completed.returncode == 2
    assert completed.stdout == ""


@pytest.mark.parametrize("target", ["evidence", "policy"])
@pytest.mark.integration
def test_output_cannot_overwrite_inputs(tmp_path, target):
    input_path = tmp_path / "evidence.json"
    original = (ROOT / "examples/diff/evidence.json").read_text(encoding="utf-8")
    input_path.write_text(original, encoding="utf-8")
    policy = tmp_path / "policy.toml"
    policy.write_text("max_findings = 2\n", encoding="utf-8")
    completed = cli(
        "review",
        "--input",
        input_path,
        "--config",
        policy,
        "--output",
        input_path if target == "evidence" else policy,
    )
    assert completed.returncode == 2
    assert input_path.read_text(encoding="utf-8") == original
    assert policy.read_text(encoding="utf-8") == "max_findings = 2\n"


@pytest.mark.integration
def test_duplicate_paths_and_unknown_fields_are_rejected(evidence, tmp_path):
    data = evidence.model_dump()
    data["files"].append(data["files"][0])
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(data), encoding="utf-8")
    assert cli("review", "--input", input_path).returncode == 2
    data["files"].pop()
    data["files"][0]["misspelled_field"] = True
    input_path.write_text(json.dumps(data), encoding="utf-8")
    assert cli("review", "--input", input_path).returncode == 2


@pytest.mark.parametrize(
    ("fixture", "valid"),
    [("valid.json", True), ("invalid.json", False)],
)
@pytest.mark.integration
def test_pr_text_format_examples(fixture, valid):
    completed = cli(
        "review",
        "--input",
        ROOT / "examples/pr-text" / fixture,
        "--config",
        ROOT / "examples/pr-text/policy.toml",
    )
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["pr_text"]["enabled"] is True
    assert report["pr_text"]["valid"] is valid
    assert (report["outcome"] == "skipped") is valid
    assert report["coverage"]["complete"] is True


@pytest.mark.parametrize(
    ("fixture", "score", "passed", "hard_failures", "soft_failures"),
    [
        ("valid.json", 100.0, True, [], []),
        ("soft_blocker.json", 80.0, True, [], ["risk-context"]),
        ("hard_blocker.json", 70.0, False, ["testing"], []),
    ],
)
@pytest.mark.integration
def test_rubric_examples(fixture, score, passed, hard_failures, soft_failures):
    completed = cli(
        "review",
        "--input",
        ROOT / "examples/rubric" / fixture,
        "--config",
        ROOT / "examples/rubric/policy.toml",
    )
    assert completed.returncode == 0
    rubric = json.loads(completed.stdout)["rubric"]
    assert rubric["score"] == score
    assert rubric["passed"] is passed
    assert rubric["failed_hard_blockers"] == hard_failures
    assert rubric["failed_soft_criteria"] == soft_failures
