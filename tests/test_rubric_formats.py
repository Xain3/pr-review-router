"""Verify JSON and TOML rubric equivalence through validation and offline replay."""

import json
import shutil

import pytest
from rubric_model_helpers import EXAMPLES
from test_cli import cli

from pr_review_router.assessment_rubric import load_assessment_rubric

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", ["rubric", "rubric-noul", "rubric-score", "rubric-mixed"])
def test_json_and_toml_examples_have_identical_validated_contracts(name):
    assert load_assessment_rubric(EXAMPLES / f"{name}.json") == load_assessment_rubric(
        EXAMPLES / f"{name}.toml"
    )


def test_json_extension_is_case_insensitive_and_existing_extensionless_toml_works(tmp_path):
    json_path = tmp_path / "rubric.JSON"
    toml_path = tmp_path / "rubric"
    json_path.write_text((EXAMPLES / "rubric.json").read_text())
    toml_path.write_text((EXAMPLES / "rubric.toml").read_text())
    assert load_assessment_rubric(json_path) == load_assessment_rubric(toml_path)


@pytest.mark.parametrize(
    "content",
    [
        '{"criteria":',
        "[]",
        '{"criteria": []}',
        '{"rubric_version": "1", "criteria": []}',
        '{"criteria": [{"criterion_id": "x", "check": "semantic"}]}',
    ],
)
def test_invalid_json_rubrics_fail_validation(content, tmp_path):
    path = tmp_path / "rubric.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_assessment_rubric(path)


@pytest.mark.integration
def test_json_corpus_reference_reuses_existing_toml_http_tapes(tmp_path):
    corpus_root = tmp_path / "corpus"
    shutil.copytree(EXAMPLES, corpus_root)
    corpus = json.loads((corpus_root / "corpus.json").read_text())
    corpus["assessment_rubric"] = "rubric.json"
    (corpus_root / "corpus.json").write_text(json.dumps(corpus))
    completed = cli(
        "evaluate",
        "--corpus",
        corpus_root / "corpus.json",
        "--providers-config",
        EXAMPLES.parent / "local-models/replay.toml",
        "--replay-dir",
        EXAMPLES / "recordings",
        "--output-dir",
        tmp_path / "results",
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["case_count"] == 9
    assert summary["totals"]["rubric_criterion_mismatches"] == 0
    assert summary["totals"]["rubric_recommendation_mismatches"] == 0


@pytest.mark.integration
def test_invalid_json_override_is_rejected_before_any_evaluation_output(tmp_path):
    rubric = tmp_path / "bad.json"
    rubric.write_text('{"criteria":')
    output = tmp_path / "results"
    completed = cli(
        "evaluate",
        "--corpus",
        EXAMPLES / "corpus.json",
        "--assessment-rubric",
        rubric,
        "--providers-config",
        EXAMPLES.parent / "local-models/replay.toml",
        "--output-dir",
        output,
    )
    assert completed.returncode == 2
    assert not output.exists()
