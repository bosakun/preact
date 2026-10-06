"""Server-owned randomized evaluator, never included in agent state/proposal prompts."""

import json
import random


def checkout_evaluator(public_probe: str, seed: int, count=64) -> str:
    rng = random.Random(seed)
    cases = [(rng.randint(-500, 1000) / 10, rng.randint(0, 1500) / 10) for _ in range(count)]
    # Force the required classes; random samples broaden the values without changing the contract.
    cases += [(-7.3, 0), (0, 0), (14.7, 16.9), (37.2, 2.1)]
    cases_json = json.dumps(cases)
    return (
        public_probe.replace("print(json.dumps(checks))", "")
        + f"""
cases = json.loads({cases_json!r})
invariants, goal = True, True
for total, discount in cases:
    try:
        actual = m.checkout(total, discount)
        if total < 0:
            invariants = False
            goal = False
        else:
            expected = max(0, total - discount)
            correct = isinstance(actual, (int, float)) and abs(actual - expected) < 1e-8
            goal = goal and correct
            if total >= discount:
                invariants = invariants and correct
    except ValueError:
        if total >= 0:
            invariants = False
            goal = False
    except Exception:
        invariants = False
        goal = False
checks["protected_invariants"] = bool(invariants)
checks["protected_goal"] = bool(goal)
print(json.dumps(checks))
"""
    )
