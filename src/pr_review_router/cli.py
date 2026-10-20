"""CLI for offline routing, explicit local experiments, and HTTP replay."""

import argparse
import json
import os
import sys
import tempfile
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import ValidationError

from .config import Policy, load_policy, resolve_config_path
from .contracts import PullRequestEvidence
from .engine import review_pull_request
from .experiments import (
    evaluate_case,
    load_corpus,
    load_corpus_rubric,
    run_experiment,
    summarize_evaluation,
)
from .model_catalog import CatalogSelectionError, load_model_catalog, resolve_catalog_path
from .provider_config import MockSettings, ProvidersConfig, ReviewSettings, load_providers_config
from .providers import MockDecisionProvider, MockReviewProvider
from .transport import ReplayMismatch, fingerprint


def _get_version() -> str:
    """Return the installed distribution version, or a source-checkout fallback.

    :returns: Installed package version, or ``"unknown"`` when unavailable.
    """
    try:
        return version("pr-review-router")
    except PackageNotFoundError:
        return "unknown"


def _write_report(path: Path, content: str) -> None:
    """Write a report atomically so an interrupted write preserves the old file.

    :param path: Destination path for the report.
    :param content: Serialized report contents to write.
    :raises OSError: If the destination cannot be written or replaced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _parse_args(
    parser: argparse.ArgumentParser, argv: list[str] | None = None
) -> argparse.Namespace | None:
    """Parse CLI options and reject output paths that would overwrite inputs.

    :param parser: Argument parser to configure and use for diagnostics.
    :param argv: Optional argument list; defaults to the process command line.
    :returns: Parsed options, or ``None`` when no command was selected.
    :raises SystemExit: If parsing fails or a path would overwrite an input.
    """
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")
    commands = parser.add_subparsers(dest="command")
    review = commands.add_parser(
        "review", help="Route evidence with mocks or explicitly configured local/hosted models."
    )
    review.add_argument("--input", type=Path, required=True, help="PR evidence JSON file.")
    review.add_argument(
        "--config",
        type=Path,
        help="Routing policy TOML file (overrides PR_REVIEW_ROUTER_CONFIG).",
    )
    review.add_argument(
        "--output", type=Path, help="Write JSON report to a file instead of stdout."
    )
    review.add_argument("--providers-config", type=Path, help="Separate provider settings TOML.")
    review.add_argument(
        "--replay", type=Path, help="Replay an exact HTTP tape without network calls."
    )
    review.add_argument("--record", type=Path, help="Explicitly write/refresh a live HTTP tape.")
    review.add_argument(
        "--experiment-output", type=Path, help="Write experiment provenance (default: reports/)."
    )
    evaluate = commands.add_parser(
        "evaluate", help="Evaluate a labeled corpus with explicit providers."
    )
    evaluate.add_argument(
        "--corpus", type=Path, required=True, help="Sanitized labeled corpus JSON."
    )
    evaluate.add_argument("--providers-config", type=Path)
    evaluate.add_argument("--config", type=Path, help="Existing routing policy TOML.")
    evaluate.add_argument(
        "--assessment-rubric",
        type=Path,
        help="Alternative rubric JSON or TOML evaluated against the same corpus labels.",
    )
    evaluate.add_argument("--output-dir", type=Path, default=Path("reports/evaluation"))
    evaluate.add_argument("--replay-dir", type=Path, help="Directory of CASE_ID.tape.json files.")
    evaluate.add_argument(
        "--record", action="store_true", help="Explicitly refresh tapes in the output directory."
    )
    assess = commands.add_parser("assess", help="Assess PR communication without a reviewer.")
    assess.add_argument("--input", type=Path, required=True)
    assess.add_argument("--assessment-rubric", type=Path, required=True)
    assess.add_argument("--config", type=Path)
    assess.add_argument("--output", type=Path)
    assess.add_argument("--experiment-output", type=Path)
    tapes = assess.add_mutually_exclusive_group()
    tapes.add_argument("--replay", type=Path)
    tapes.add_argument("--record", type=Path)
    report = commands.add_parser("report", help="Explain a finalized communication assessment.")
    report.add_argument("--input", type=Path, required=True, help="Final AssessmentReport JSON.")
    report.add_argument("--output", type=Path, help="Markdown output (default: stdout).")
    report.add_argument("--json", action="store_true", help="Emit a typed explanation artifact.")
    report.add_argument(
        "--reporter-config", type=Path, help="Separate named reporter catalog TOML."
    )
    report.add_argument("--reporter", help="Explicit opt-in Ollama reporter model alias.")
    for command in (review, evaluate, assess):
        command.add_argument("--models-config", type=Path, help="Named model catalog TOML.")
        command.add_argument("--profile", help="Catalog profile name.")
        command.add_argument("--decider", help="Catalog decision-model name.")
        if command is not assess:
            command.add_argument("--reviewer", help="Catalog review-model name (both depths).")
        command.add_argument(
            "--model-override",
            action="append",
            default=[],
            metavar="ROLE.FIELD=TOML",
            help="Override a decision/review setting; repeat for distinct fields.",
        )
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return None
    if args.command in {"assess", "report"}:
        return args
    try:
        args.config = resolve_config_path(args.config)
    except ValueError as error:
        parser.error(str(error))
    catalog_options = bool(
        args.models_config or args.profile or args.decider or args.reviewer or args.model_override
    )
    if args.providers_config and catalog_options:
        parser.error("--providers-config cannot be combined with catalog options")
    catalog_path = resolve_catalog_path(args.models_config)
    args.catalog_path = None if args.providers_config else catalog_path
    try:
        args.provider_settings = (
            load_providers_config(args.providers_config)
            if args.providers_config
            else load_model_catalog(catalog_path).resolve(
                profile=args.profile,
                decider=args.decider,
                reviewer=args.reviewer,
                overrides=args.model_override,
            )
        )
    except CatalogSelectionError as error:
        parser.error(str(error))
    except (OSError, ValueError):
        parser.error("could not read valid UTF-8 provider configuration or model catalog/selection")
    args.use_providers = bool(
        args.providers_config
        or catalog_options
        or not isinstance(args.provider_settings.decision, MockSettings)
        or not isinstance(args.provider_settings.review, MockSettings)
    )
    if args.command == "review":
        if not args.use_providers and (args.record or args.replay or args.experiment_output):
            parser.error(
                "experiment options require --providers-config or explicit catalog selection"
            )
        if args.record and args.replay:
            parser.error("--record and --replay are mutually exclusive")
        _protect_paths(
            parser,
            [args.output, args.record, args.experiment_output],
            [args.input, args.config, args.providers_config, args.catalog_path, args.replay],
        )
    elif args.record and args.replay_dir:
        parser.error("--record and --replay-dir are mutually exclusive")
    return args


def _protect_paths(
    parser: argparse.ArgumentParser,
    outputs: list[Path | None],
    inputs: list[Path | None],
) -> None:
    """Reject output collisions and source replacement before any model calls.

    :param parser: Argument parser used for a concise usage diagnostic.
    :param outputs: All proposed output paths.
    :param inputs: Protected evidence, policy, provider, label, and recording paths.
    :raises SystemExit: If outputs collide or would overwrite an input.
    """
    targets = [path.resolve() for path in outputs if path is not None]
    sources = {path.resolve() for path in inputs if path is not None}
    if len(targets) != len(set(targets)) or sources.intersection(targets):
        parser.error("output paths must differ from each other and from evidence and policy inputs")


def _provider_inputs(
    config: ProvidersConfig, policy: Policy, parser: argparse.ArgumentParser
) -> ProvidersConfig:
    """Validate explicit provider selection and the local experiment policy.

    :param config: Resolved provider settings.
    :param policy: Existing policy to enforce experiment restrictions against.
    :param parser: CLI diagnostic parser.
    :returns: Validated independent provider configuration.
    :raises SystemExit: If configuration cannot be read or violates experiment restrictions.
    """
    if policy.allow_direct_acceptance or policy.allow_direct_rejection:
        parser.error("local experiments require direct acceptance and rejection to be disabled")
    return config


def _evaluate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Run a validated corpus and export individual artifacts plus aggregate metrics.

    :param args: Parsed evaluation arguments.
    :param parser: CLI diagnostic parser.
    :returns: Zero for completed advisory evaluation, regardless of model quality.
    :raises SystemExit: If inputs, replay, or output paths fail validation.
    """
    try:
        policy = load_policy(args.config)
        corpus, evidence, sources = load_corpus(args.corpus)
        rubric, review_texts, rubric_sources = load_corpus_rubric(
            args.corpus, corpus, rubric_path=args.assessment_rubric
        )
    except (OSError, ValueError):
        parser.error("could not read valid UTF-8 corpus, evidence, or policy")
    config = _provider_inputs(args.provider_settings, policy, parser)
    if rubric and config.decision.backend not in {"ollaya", "jev", "ollama"}:
        parser.error("semantic rubric evaluation requires a typed decision provider")
    replay_paths = [
        args.replay_dir / f"{case.case_id}.tape.json" if args.replay_dir else None
        for case in corpus.cases
    ]
    output_paths = [args.output_dir / "summary.json"]
    for case in corpus.cases:
        output_paths.extend(
            args.output_dir / f"{case.case_id}.{suffix}.json" for suffix in ("report", "experiment")
        )
        if args.record:
            output_paths.append(args.output_dir / f"{case.case_id}.tape.json")
    _protect_paths(
        parser,
        output_paths,
        [
            args.corpus,
            args.config,
            args.providers_config,
            args.catalog_path,
            *sources,
            *rubric_sources,
            *replay_paths,
        ],
    )
    metrics = []
    try:
        for case, item, replay, review_text in zip(
            corpus.cases, evidence, replay_paths, review_texts, strict=True
        ):
            result = run_experiment(
                item,
                policy,
                config,
                replay=replay,
                record=args.record,
                rubric=rubric,
                review_text=review_text,
            )
            _write_report(
                args.output_dir / f"{case.case_id}.report.json",
                result.report.model_dump_json(indent=2) + "\n",
            )
            _write_report(
                args.output_dir / f"{case.case_id}.experiment.json",
                json.dumps(result.artifact, indent=2) + "\n",
            )
            if result.tape:
                _write_report(
                    args.output_dir / f"{case.case_id}.tape.json",
                    result.tape.model_dump_json(indent=2) + "\n",
                )
            metrics.append(evaluate_case(case, result))
        summary = summarize_evaluation(corpus, metrics)
        summary["mode"] = "replay" if args.replay_dir else "live"
        _write_report(args.output_dir / "summary.json", json.dumps(summary, indent=2) + "\n")
        sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    except ReplayMismatch:
        parser.error("replay does not match this experiment; refresh recordings explicitly")
    except (OSError, ValueError):
        parser.error("could not read replay or write evaluation outputs")
    return 0


def _load_inputs(
    input_path: Path,
    config_path: Path | None,
    parser: argparse.ArgumentParser,
) -> tuple[Policy, PullRequestEvidence]:
    """Load and validate policy/evidence while keeping diagnostics concise.

    :param input_path: JSON file containing pull request evidence.
    :param config_path: Optional TOML file containing routing policy.
    :param parser: Argument parser used to report invalid input.
    :returns: Validated policy and pull request evidence.
    :raises SystemExit: If input files cannot be read or validated.
    """
    try:
        policy = load_policy(config_path)
        data = json.loads(input_path.read_text(encoding="utf-8"))
        evidence = PullRequestEvidence.model_validate(data)
    except ValidationError as error:
        # Avoid echoing untrusted evidence values in diagnostics.
        locations = ", ".join(
            ".".join(str(part) for part in item["loc"]) or "document"
            for item in error.errors(include_input=False, include_url=False)[:5]
        )
        parser.error(f"invalid evidence or policy fields: {locations}")
    except (json.JSONDecodeError, tomllib.TOMLDecodeError):
        parser.error("invalid JSON evidence or TOML policy")
    except (OSError, UnicodeError):
        parser.error("could not read evidence or policy as UTF-8")
    return policy, evidence


def main(argv: list[str] | None = None) -> int:
    """Run advisory routing or an explicitly configured local evaluation.

    :param argv: Optional argument list; defaults to the process command line.
    :returns: Process exit status; assess distinguishes verdicts and suggestions.
    :raises SystemExit: If arguments, inputs, or output paths are invalid.
    """
    parser = argparse.ArgumentParser(description="Advisory pull request review router.")
    args = _parse_args(parser, argv)
    if args is None:
        return 0
    if args.command == "report":
        return _report(args, parser)
    if args.command == "assess":
        return _assess(args, parser)
    if isinstance(args.provider_settings.decision, ReviewSettings):
        sys.stderr.write(
            "warning: general LLM deciders are a non-optimal compatibility fallback. "
            "Prefer a native System One decider (ollaya or jev); generated probabilities "
            "and confidence are self-reported, not native or calibrated.\n"
        )
    if args.command == "evaluate":
        return _evaluate(args, parser)
    policy, evidence = _load_inputs(args.input, args.config, parser)
    result = None
    artifact_path = None
    if args.use_providers:
        config = _provider_inputs(args.provider_settings, policy, parser)
        # Derive a safe default name even when supplied commit identifiers contain path syntax.
        artifact_path = args.experiment_output or Path(
            f"reports/experiment-{evidence.number}-{fingerprint(evidence.head_sha)[:12]}.json"
        )
        _protect_paths(
            parser,
            [args.output, args.record, artifact_path],
            [args.input, args.config, args.providers_config, args.catalog_path, args.replay],
        )
        try:
            result = run_experiment(
                evidence, policy, config, replay=args.replay, record=bool(args.record)
            )
        except ReplayMismatch:
            parser.error("replay does not match this experiment; refresh recordings explicitly")
        except (OSError, ValueError):
            parser.error("could not read a valid replay recording")
        report = result.report
    else:
        report = review_pull_request(evidence, policy, MockDecisionProvider(), MockReviewProvider())
    content = report.model_dump_json(indent=2) + "\n"
    try:
        if result is not None and artifact_path is not None:
            _write_report(artifact_path, json.dumps(result.artifact, indent=2) + "\n")
            if args.record and result.tape:
                _write_report(args.record, result.tape.model_dump_json(indent=2) + "\n")
        if args.output:
            _write_report(args.output, content)
        else:
            sys.stdout.write(content)
    except OSError:
        parser.error("could not write the report")
    return 0


def _report(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Explain a saved assessment independently of its verdict exit code.

    :param args: Reporter-only arguments.
    :param parser: Safe command diagnostic formatter.
    :returns: Zero when rendered (including fallback); command/I/O errors exit 2.
    """
    from .assessment_engine import AssessmentReport
    from .reporter import OllamaReporter, explain_assessment, load_reporter_settings

    if bool(args.reporter) != bool(args.reporter_config):
        parser.error("--reporter and --reporter-config must be supplied together")
    _protect_paths(parser, [args.output], [args.input, args.reporter_config])
    try:
        report = AssessmentReport.model_validate_json(args.input.read_bytes())
        provider = (
            OllamaReporter(load_reporter_settings(args.reporter_config, args.reporter))
            if args.reporter
            else None
        )
    except (OSError, UnicodeError, ValueError):
        parser.error("could not load a finalized assessment or reporter configuration")
    result = explain_assessment(report, provider)
    content = result.model_dump_json(indent=2) + "\n" if args.json else result.markdown
    try:
        if args.output:
            _write_report(args.output, content)
        else:
            sys.stdout.write(content)
    except OSError:
        parser.error("could not write the explanation")
    return 0


def _assess(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Write a decider-only communication report using existing safe output handling.

    :param args: Assessment arguments, separate from legacy review selections.
    :param parser: CLI error formatter.
    :returns: 0 for accepted, 3 for accepted with suggestions, 1 for rejected,
        or 4 for unresolved; invalid commands and output errors exit 2.
    """
    from .assessment_rubric import load_assessment_rubric
    from .assessment_runtime import run_assessment
    from .model_catalog import load_decider_settings

    try:
        config_path = resolve_config_path(args.config)
        policy, evidence = _load_inputs(args.input, config_path, parser)
        rubric = load_assessment_rubric(args.assessment_rubric)
        catalog_path = resolve_catalog_path(args.models_config)
        settings = load_decider_settings(
            catalog_path, profile=args.profile, decider=args.decider, overrides=args.model_override
        )
        if any(c.target == "review" for c in rubric.criteria):
            raise ValueError("communication rubrics cannot assess supplied reviews")
    except (OSError, UnicodeError, ValueError):
        parser.error("could not load valid communication rubric, policy, or decider settings")
    artifact_path = args.experiment_output or Path(
        f"reports/assessment-{evidence.number}-{fingerprint(evidence.head_sha)[:12]}.json"
    )
    _protect_paths(
        parser,
        [args.output, artifact_path, args.record],
        [
            args.input,
            config_path,
            args.assessment_rubric,
            catalog_path,
            args.replay,
        ],
    )
    if isinstance(settings, ReviewSettings):
        sys.stderr.write("warning: LLM decider probabilities are self-reported, not calibrated.\n")
    try:
        result = run_assessment(
            evidence, policy, rubric, settings, replay=args.replay, record=bool(args.record)
        )
        _write_report(artifact_path, json.dumps(result.artifact, indent=2) + "\n")
        if args.record and result.tape:
            _write_report(args.record, result.tape.model_dump_json(indent=2) + "\n")
        content = result.report.model_dump_json(indent=2) + "\n"
        if args.output:
            _write_report(args.output, content)
        else:
            sys.stdout.write(content)
    except ReplayMismatch:
        parser.error("replay does not match this assessment; refresh recordings explicitly")
    except (OSError, UnicodeError, ValueError):
        parser.error("could not execute assessment or write its outputs")
    if result.report.verdict == "rejected":
        return 1
    if result.report.verdict == "unresolved":
        return 4
    return 3 if result.report.suggestions else 0


if __name__ == "__main__":
    raise SystemExit(main())
