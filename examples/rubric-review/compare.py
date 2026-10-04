"""Compare native question formats against identical rubric evidence and labels."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Run three evaluations, replaying synthetic tapes unless live mode is explicit.

    :raises SystemExit: If arguments or an evaluation fail.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly invoke local models.")
    parser.add_argument("--record", action="store_true", help="Record live HTTP exchanges.")
    parser.add_argument("--providers-config", type=Path)
    parser.add_argument("--replay-dir", type=Path, help="Root containing choice/noul/score tapes.")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/rubric-comparison"))
    args = parser.parse_args()
    if args.record and not args.live:
        parser.error("--record requires --live")
    if args.live and args.replay_dir:
        parser.error("--live and --replay-dir are mutually exclusive")
    root = Path(__file__).resolve().parent
    config = args.providers_config or root.parent / "local-models" / (
        "providers.toml" if args.live else "replay.toml"
    )
    comparison = {}
    for kind in ("choice", "noul", "score"):
        command = [
            sys.executable,
            "-m",
            "pr_review_router.cli",
            "evaluate",
            "--corpus",
            str(root / "corpus.json"),
            "--providers-config",
            str(config),
            "--assessment-rubric",
            str(root / ("rubric.json" if kind == "choice" else f"rubric-{kind}.json")),
            "--output-dir",
            str(args.output_dir / kind),
        ]
        if args.record:
            command.append("--record")
        if not args.live:
            tapes = (
                args.replay_dir / kind
                if args.replay_dir
                else root / ("recordings" if kind == "choice" else f"recordings-{kind}")
            )
            command.extend(["--replay-dir", str(tapes)])
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode:
            sys.stderr.write(completed.stderr)
            raise SystemExit(completed.returncode)
        summary = json.loads(completed.stdout)
        comparison[kind] = {
            "mode": summary["mode"],
            "label_status": summary["label_status"],
            "case_count": summary["case_count"],
            "totals": summary["totals"],
            "cases": summary["cases"],
        }
    print(
        json.dumps(
            {
                "formats": comparison,
                "limitations": "Thresholds are experimental. Synthetic replay verifies integration, "
                "not relative model quality. Live comparisons require human-reviewed labels.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
