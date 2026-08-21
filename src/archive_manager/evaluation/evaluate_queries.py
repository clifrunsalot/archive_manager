"""Run the archive query evaluation fixture."""

import argparse
import json
from pathlib import Path

from archive_manager.evaluation.evaluation import evaluate_file
from archive_manager.retrieval.query import answer


def main():
    parser = argparse.ArgumentParser(description="Evaluate archive query cases")
    parser.add_argument("--fixture", type=Path, required=True, help="JSON evaluation fixture")
    args = parser.parse_args()
    result = evaluate_file(args.fixture, answer)
    print(json.dumps(result, indent=2))
    return 0 if result["case_count"] == result["answer_pass_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
