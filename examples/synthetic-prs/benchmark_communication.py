"""Run the communication-only benchmark without changing the legacy corpus."""

from pathlib import Path

from pr_review_router.communication_benchmark import benchmark_main

if __name__ == "__main__":
    benchmark_main(Path(__file__).resolve().parent)
