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
from .provider_config import ProvidersConfig, load_providers_config
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
        "review", help="Route evidence with offline mocks or explicitly configured local models."
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
    evaluate = commands.add_parser("evaluate", help="Evaluate a labeled corpus with local models.")
    evaluate.add_argument(
        "--corpus", type=Path, required=True, help="Sanitized labeled corpus JSON."
    )
    evaluate.add_argument("--providers-config", type=Path, required=True)
    evaluate.add_argument("--config", type=Path, help="Existing routing policy TOML.")
    evaluate.add_argument(
        "--assessment-rubric",
        type=Path,
        help="Alternative rubric TOML evaluated against the same corpus labels.",
    )
    evaluate.add_argument("--output-dir", type=Path, default=Path("reports/evaluation"))
    evaluate.add_argument("--replay-dir", type=Path, help="Directory of CASE_ID.tape.json files.")
    evaluate.add_argument(
        "--record", action="store_true", help="Explicitly refresh tapes in the output directory."
    )
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return None
    try:
        args.config = resolve_config_path(args.config)
    except ValueError as error:
        parser.error(str(error))
    if args.command == "review":
        if not args.providers_config and (args.record or args.replay or args.experiment_output):
            parser.error("experiment options require --providers-config")
        if args.record and args.replay:
            parser.error("--record and --replay are mutually exclusive")
        _protect_paths(
            parser,
            [args.output, args.record, args.experiment_output],
            [args.input, args.config, args.providers_config, args.replay],
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
    path: Path, policy: Policy, parser: argparse.ArgumentParser
) -> ProvidersConfig:
    """Validate explicit provider selection and the local experiment policy.

    :param path: Provider TOML path.
    :param policy: Existing policy to enforce experiment restrictions against.
    :param parser: CLI diagnostic parser.
    :returns: Validated independent provider configuration.
    :raises SystemExit: If configuration cannot be read or violates experiment restrictions.
    """
    if policy.allow_direct_acceptance or policy.allow_direct_rejection:
        parser.error("local experiments require direct acceptance and rejection to be disabled")
    try:
        return load_providers_config(path)
    except (OSError, ValueError):
        parser.error("could not read valid UTF-8 provider configuration")


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
    config = _provider_inputs(args.providers_config, policy, parser)
    if rubric and config.decision.backend != "ollaya":
        parser.error("semantic rubric evaluation requires an Ollaya decision provider")
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
        [args.corpus, args.config, args.providers_config, *sources, *rubric_sources, *replay_paths],
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
    :returns: Process exit status, with zero for a successful command.
    :raises SystemExit: If arguments, inputs, or output paths are invalid.
    """
    parser = argparse.ArgumentParser(description="Advisory pull request review router.")
    args = _parse_args(parser, argv)
    if args is None:
        return 0
    if args.command == "evaluate":
        return _evaluate(args, parser)
    policy, evidence = _load_inputs(args.input, args.config, parser)
    result = None
    if args.providers_config:
        config = _provider_inputs(args.providers_config, policy, parser)
        # Derive a safe default name even when supplied commit identifiers contain path syntax.
        artifact_path = args.experiment_output or Path(
            f"reports/experiment-{evidence.number}-{fingerprint(evidence.head_sha)[:12]}.json"
        )
        _protect_paths(
            parser,
            [args.output, args.record, artifact_path],
            [args.input, args.config, args.providers_config, args.replay],
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
        if result:
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


if __name__ == "__main__":
    raise SystemExit(main())
