"""Keep explanations faithful and isolated from authoritative assessment results."""

import json

import httpx
import pytest
from test_cli import ROOT, cli
from test_communication_assessment import evidence, execute, rubric

from pr_review_router.assessment_engine import assess_pull_request
from pr_review_router.config import Policy
from pr_review_router.reporter import (
    Interpretation,
    OllamaReporter,
    ReporterOutput,
    ReporterPlan,
    ReporterSettings,
    explain_assessment,
    load_reporter_settings,
)
from pr_review_router.transport import fingerprint

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "statuses,verdict",
    [
        ({}, "accepted"),
        ({"accuracy": "failed", "testing": "failed"}, "rejected"),
        ({"accuracy": "uncertain"}, "unresolved"),
        ({"testing": "failed"}, "accepted"),
    ],
)
def test_recorded_facts_and_integrity(statuses, verdict):
    """Render each verdict, suggestions, provenance, and all recorded gate facts.

    :param statuses: Synthetic decider answers.
    :param verdict: Expected authoritative result.
    """
    report = execute(statuses)[0].report
    original = report.model_dump_json()
    result = explain_assessment(report)
    assert result == explain_assessment(report)
    assert result.mode == "deterministic"
    assert f"assessment: {verdict}" in result.markdown
    assert result.assessment_fingerprint == fingerprint(report.model_dump(mode="json"))
    for gate in report.gates:
        assert f"{gate.gate_id}: {gate.status}" in result.markdown
    for criterion in report.criteria:
        assert criterion.criterion_id in result.markdown
        assert criterion.source in result.markdown
    if report.suggestions:
        assert "Explain relevant validation." in result.markdown
    assert "No tests were run by this reporter" in result.markdown
    assert report.model_dump_json() == original


def test_preflight_failure_and_unavailable_inference():
    """Render missing patch coverage and early failure without invoking inference."""
    item = evidence().model_copy(update={"files_complete": False, "title": "invalid"})
    report = assess_pull_request(item, Policy(), rubric())
    text = explain_assessment(report).markdown
    assert "assessment: rejected" in text
    assert "preflight:coverage: uncertain" in text
    assert "Not assessed:" in text
    assert "unavailable" in text
    assert "Qualified interpretation" not in text


class Organizer:
    """Supply constrained plans while attempting to mutate a private input copy."""

    def explain(self, source):
        """Organize facts and tamper with isolated report fields.

        :param source: Detached reporter input.
        :returns: Valid plan with qualified uncertainty framing.
        """
        source.report.verdict = "accepted"
        source.report.gates.clear()
        source.report.criteria.clear()
        uncertain = next(f.fact_id for f in source.facts if f.uncertain)
        return ReporterPlan(
            fact_order=[f.fact_id for f in reversed(source.facts)],
            interpretations=[Interpretation(fact_id=uncertain)],
        )


def test_provider_mutation_cannot_change_report_or_rendered_verdict():
    """Never trust a provider's modified copy as rendering authority."""
    report = execute({"accuracy": "uncertain"})[0].report
    original = report.model_dump_json()
    result = explain_assessment(report, Organizer())
    assert result.mode == "model"
    assert "assessment: unresolved" in result.markdown
    assert "Qualified interpretation" in result.markdown
    assert "not an additional finding or rejection reason" in result.markdown
    assert report.model_dump_json() == original


@pytest.mark.parametrize("failure", ["raise", "omit", "invent", "duplicate", "prose", "interpret"])
def test_invalid_plans_fall_back_without_leaking(failure):
    """Reject invented findings, omitted facts, or unsupported interpretations.

    :param failure: Scripted provider contract violation.
    """

    class Invalid:
        """An untrusted provider response."""

        def explain(self, source):
            """Violate one contract constraint.

            :param source: Isolated report facts.
            :returns: Deliberately malformed response.
            :raises RuntimeError: When simulating a failed provider.
            """
            if failure == "raise":
                raise RuntimeError("secret upstream text")
            ids = [f.fact_id for f in source.facts]
            if failure == "omit":
                ids.pop()
            if failure == "invent":
                ids[0] = "invented rejection reason"
            if failure == "duplicate":
                ids[-1] = ids[0]
            data = {"fact_order": ids}
            if failure == "prose":
                data["markdown"] = "Tests passed; merge approved."
            if failure == "interpret":
                data["interpretations"] = [{"fact_id": ids[0]}]
            return data

    report = execute()[0].report
    original = report.model_dump_json()
    result = explain_assessment(report, Invalid())
    assert result.mode == "fallback"
    assert result.diagnostic == "reporter_unavailable_or_invalid"
    assert result.markdown == explain_assessment(report).markdown
    assert "secret" not in result.model_dump_json()
    assert report.model_dump_json() == original


@pytest.mark.parametrize("alias", ["qwen-coding", "qwen-general"])
def test_explicit_ollama_models_and_grounded_generation(alias):
    """Exercise both supported identifiers at a synthetic HTTP boundary.

    :param alias: Explicit experimental model alias.
    """
    settings = load_reporter_settings(ROOT / "examples/reporter/models.toml", alias)
    calls = []

    def handle(request):
        """Respond with a grounded plan based on the finalized snapshot.

        :param request: Synthetic Ollama request.
        :returns: Complete, structured Ollama response.
        """
        calls.append(json.loads(request.content))
        body = calls[-1]
        assert request.url.path == "/api/chat"
        assert body["model"] == settings.model
        source = json.loads(body["messages"][1]["content"])
        assert source["report"]["report_type"] == "pr_communication"
        assert "files" not in source
        content = {"fact_order": [f["fact_id"] for f in source["facts"]]}
        return httpx.Response(
            200,
            json={"done": True, "done_reason": "stop", "message": {"content": json.dumps(content)}},
        )

    provider = OllamaReporter(settings, http_transport=httpx.MockTransport(handle))
    report = execute()[0].report
    result = explain_assessment(report, provider)
    assert result.mode == "model"
    assert len(calls) == 1
    assert result.markdown == explain_assessment(report).markdown


@pytest.mark.parametrize("failure", ["timeout", "http", "json", "schema", "truncated", "limit"])
def test_ollama_failures(failure):
    """Keep HTTP, response-limit, and generation failures outside the machine result.

    :param failure: Scripted adapter failure.
    """

    def handle(request):
        """Return a failed or invalid generation.

        :param request: Synthetic HTTP request.
        :returns: Deliberately invalid response.
        :raises httpx.ReadTimeout: For simulated timeout.
        """
        if failure == "timeout":
            raise httpx.ReadTimeout("private response")
        if failure == "http":
            return httpx.Response(503)
        if failure == "json":
            return httpx.Response(200, text="not json")
        if failure == "limit":
            return httpx.Response(200, text="x" * 10000)
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "length" if failure == "truncated" else "stop",
                "message": {"content": '{"verdict": "accepted"}'},
            },
        )

    settings = ReporterSettings(
        endpoint="http://localhost:11434", model="synthetic", max_response_bytes=2048
    )
    report = execute({"accuracy": "failed"})[0].report
    result = explain_assessment(
        report, OllamaReporter(settings, http_transport=httpx.MockTransport(handle))
    )
    assert result.mode == "fallback"
    assert result.markdown == explain_assessment(report).markdown


def test_input_budget_and_literal_recorded_data():
    """Skip oversized model inputs and escape hostile recorded Markdown/HTML."""
    report = execute()[0].report
    report.diagnostics.append("<script>bad</script>\n## approved [link](https://example.com)")
    settings = ReporterSettings(
        endpoint="http://localhost:11434", model="synthetic", max_input_bytes=1
    )
    result = explain_assessment(report, OllamaReporter(settings))
    assert result.mode == "fallback"
    assert "<script>" not in result.markdown
    assert "\\#\\# approved" in result.markdown
    assert "\\[link\\]" in result.markdown


def test_report_cli_saved_assessment_and_path_protection(tmp_path):
    """Expose a separate reporter path without changing assessment exit semantics.

    :param tmp_path: Isolated temporary directory.
    """
    source = tmp_path / "assessment.json"
    report = execute({"accuracy": "failed"})[0].report
    source.write_text(report.model_dump_json())
    original = source.read_bytes()
    result = cli("report", "--input", str(source), "--json")
    assert result.returncode == 0
    artifact = ReporterOutput.model_validate_json(result.stdout)
    assert artifact.mode == "deterministic"
    assert "assessment: rejected" in artifact.markdown
    assert cli("report", "--input", str(source), "--output", str(source)).returncode == 2
    assert cli("report", "--input", str(source), "--reporter", "qwen-coding").returncode == 2
    assert (
        cli(
            "report", "--input", str(source), "--reporter-config", "examples/reporter/models.toml"
        ).returncode
        == 2
    )
    destination = tmp_path / "comment.md"
    assert cli("report", "--input", str(source), "--output", str(destination)).returncode == 0
    assert "assessment: rejected" in destination.read_text()
    assert source.read_bytes() == original
