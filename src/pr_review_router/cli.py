"""Offline CLI for validated evidence and structured advisory reports."""

import argparse
import json
import os
import sys
import tempfile
import tomllib
from importlib.metadata import version
from pathlib import Path

from pydantic import ValidationError

from .config import load_policy
from .contracts import PullRequestEvidence
from .engine import review_pull_request
from .providers import MockDecisionProvider, MockReviewProvider


def _write_report(path: Path, content: str) -> None:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advisory pull request review router.")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('pr-review-router')}"
    )
    commands = parser.add_subparsers(dest="command")
    review = commands.add_parser(
        "review", help="Review evidence using deterministic offline mocks."
    )
    review.add_argument("--input", type=Path, required=True, help="PR evidence JSON file.")
    review.add_argument(
        "--config", type=Path, help="Routing policy TOML file (defaults if omitted)."
    )
    review.add_argument(
        "--output", type=Path, help="Write JSON report to a file instead of stdout."
    )
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.output and args.output.resolve() in {
        args.input.resolve(),
        args.config.resolve() if args.config else None,
    }:
        parser.error("--output must differ from the evidence and policy paths")
    try:
        policy = load_policy(args.config)
        data = json.loads(args.input.read_text(encoding="utf-8"))
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
    report = review_pull_request(evidence, policy, MockDecisionProvider(), MockReviewProvider())
    content = report.model_dump_json(indent=2) + "\n"
    try:
        if args.output:
            _write_report(args.output, content)
        else:
            sys.stdout.write(content)
    except OSError:
        parser.error("could not write the report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
