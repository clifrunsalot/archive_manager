"""Fixture-driven evaluation for query planning and answer regressions."""

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Callable

from archive_manager.retrieval.query_planner import plan_query


@dataclass(frozen=True)
class EvaluationCase:
    question: str
    expected_intent: str
    expected_substrings: list[str]


def evaluate_case(case: EvaluationCase, answer_func: Callable[[str], str]) -> dict:
    """Evaluate one question without assuming the answer uses an LLM."""
    started = time.perf_counter()
    answer = answer_func(case.question)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    plan = plan_query(case.question)
    missing = [value for value in case.expected_substrings if value not in answer]
    return {
        "question": case.question,
        "intent": plan.intent,
        "expected_intent": case.expected_intent,
        "intent_pass": plan.intent == case.expected_intent,
        "missing_substrings": missing,
        "answer_pass": not missing,
        "elapsed_ms": elapsed_ms,
    }


def load_cases(path: Path) -> list[EvaluationCase]:
    """Load JSON evaluation cases."""
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    return [EvaluationCase(**case) for case in raw_cases]


def evaluate_file(path: Path, answer_func: Callable[[str], str]) -> dict:
    """Evaluate all cases and return aggregate metrics."""
    results = [evaluate_case(case, answer_func) for case in load_cases(path)]
    return {
        "case_count": len(results),
        "intent_pass_count": sum(result["intent_pass"] for result in results),
        "answer_pass_count": sum(result["answer_pass"] for result in results),
        "results": results,
    }
