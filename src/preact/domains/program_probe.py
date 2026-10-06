"""Standalone protected evaluator: repository code is data, never evaluator authority."""

import importlib.util
import json
import math
import sys
from pathlib import Path


def equivalent(actual, expected):
    if type(expected) is bool:
        return type(actual) is bool and actual == expected
    if isinstance(expected, (int, float)):
        return type(actual) in (int, float) and math.isclose(
            actual, expected, rel_tol=1e-9, abs_tol=1e-9
        )
    return actual == expected


def evaluate(source, oracle):
    spec = importlib.util.spec_from_file_location("subject", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, oracle["target"])
    results = []
    for case in oracle["cases"]:
        try:
            result = equivalent(function(*case["args"]), case["expected"])
        except Exception:
            result = False
        results.append(result)
    safety = all(result for result, case in zip(results, oracle["cases"]) if case["safety"])
    return {
        "checks": {"syntax": True, "invariants": safety},
        "goal_complete": all(results),
        "metrics": {
            "goal_progress": float(all(results)),
            "test_pass_fraction": sum(results) / len(results),
        },
        "evidence": {"protected_cases": len(results), "passed_cases": sum(results)},
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(sys.argv[1], json.loads(Path(sys.argv[2]).read_text()))))
