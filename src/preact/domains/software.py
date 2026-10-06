from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from preact.core.models import Action, Observation, State, Task
from preact.domains.evaluation import checkout_evaluator

BASE = "def checkout(total, discount):\n    if total < 0:\n        raise ValueError('negative total')\n    return total - discount\n"
SHORTCUT = "def checkout(total, discount):\n    return max(0, total - discount)\n"
PREPARE = "def _validate(total, discount):\n    if total < 0 or discount < 0:\n        raise ValueError('negative amount')\n\ndef checkout(total, discount):\n    if total < 0:\n        raise ValueError('negative total')\n    return total - discount\n"
SAFE = "def _validate(total, discount):\n    if total < 0 or discount < 0:\n        raise ValueError('negative amount')\n\ndef checkout(total, discount):\n    _validate(total, discount)\n    return max(0, total - discount)\n"
REPAIR = "def checkout(total, discount):\n    if total < 0 or discount < 0:\n        raise ValueError('negative amount')\n    return max(0, total - discount)\n"
ALLOWED = {BASE, SHORTCUT, PREPARE, SAFE, REPAIR}

PROBE = """import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("checkout", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
checks = {}
for name, total, discount, expected in [("ordinary", 10, 2, 8), ("zero", 0, 0, 0), ("over_discount", 10, 20, 0)]:
    try: checks[name] = m.checkout(total, discount) == expected
    except Exception: checks[name] = False
try:
    m.checkout(-1, 0)
    checks["negative_total"] = False
except ValueError: checks["negative_total"] = True
except Exception: checks["negative_total"] = False
print(json.dumps(checks))
"""


async def probe(source: str, seed: int = 0) -> dict[str, bool]:
    # This runner accepts ONLY enumerated first-party examples, never model-generated code.
    if source not in ALLOWED:
        raise ValueError(
            "Local execution is restricted to trusted bundled source; use ConTree for agent code"
        )
    with tempfile.TemporaryDirectory(prefix="preact-probe-") as folder:
        path = Path(folder)
        (path / "checkout.py").write_text(source)
        (path / "probe.py").write_text(checkout_evaluator(PROBE, seed))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(path / "probe.py"),
            str(path / "checkout.py"),
            cwd=folder,
            env={"PATH": os.defpath},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError("Trusted probe execution failed")
        return json.loads(out)


class SoftwareWorld:
    # Successful local proposal generation is pure; overrides using external work
    # must preserve complete usage receipts or withdraw this certification.
    proposal_usage_complete = True

    def __init__(self, seed: int = 0):
        self.task = Task(
            id="checkout-repair",
            domain="software",
            title="Repair checkout invariants",
            goal="Cap discounts without removing negative-total validation",
            seed=seed,
            required_checks=["syntax", "regressions"],
            max_steps=4,
        )
        self.payload = {"files": {"checkout.py": BASE}, "stage": "initial", "goal_progress": 0.0}
        self.receipts = {}

    async def observe(self):
        return State.create("software", self.payload.copy(), "trusted-local-repository")

    async def propose(self, state, width):
        source = state.payload["files"]["checkout.py"]
        if source == PREPARE:
            options = [
                ("Integrate validation and cap discounts", SAFE, "repaired"),
                ("Skip validation for a smaller diff", SHORTCUT, "shortcut"),
            ]
        else:
            options = [
                ("Quick fix: clamp the result", SHORTCUT, "shortcut"),
                ("Prepare a reusable validator", PREPARE, "prepared"),
            ]
        return [
            Action(
                name=name,
                kind="patch",
                state_id=state.id,
                payload={"files": {"checkout.py": code}, "stage": stage},
                rationale="Compare executable outcomes before applying the patch",
            )
            for name, code, stage in options[:width]
            if code != source
        ]

    def validate(self, state, action):
        if action.state_id != state.id or action.kind != "patch":
            raise ValueError("Invalid software action")
        files = action.payload.get("files", {})
        if set(files) != {"checkout.py"} or files["checkout.py"] not in ALLOWED:
            raise ValueError("Untrusted patches require the remote Sandbox domain")
        ast.parse(files["checkout.py"])

    def materialize(self, state, action):
        self.validate(state, action)
        payload = {
            "files": action.payload["files"],
            "stage": action.payload["stage"],
            "goal_progress": 1.0
            if action.payload["files"]["checkout.py"] in {SAFE, REPAIR}
            else 0.25,
        }
        return State.create(
            "software",
            payload,
            "patch-materialization",
            kind="hypothetical",
            parent_id=state.id,
            uncertainty=state.uncertainty,
        )

    def complete(self, state):
        return bool(state.payload.get("goal_complete"))

    async def execute(self, action, receipt):
        if receipt in self.receipts:
            return self.receipts[receipt]
        state = await self.observe()
        successor = self.materialize(state, action)
        checks = await probe(successor.payload["files"]["checkout.py"], self.task.seed)
        self.payload = successor.payload
        self.payload["goal_complete"] = all(checks.values())
        observed = await self.observe()
        regressions = all(
            checks[k] for k in ["ordinary", "zero", "negative_total", "protected_invariants"]
        )
        result = Observation(
            cost_usd=0,
            state=observed,
            success=self.complete(observed),
            unsafe=not regressions,
            checks={**checks, "action_success": regressions},
            receipt=receipt,
            metrics={
                "tests_passed": sum(checks.values()),
                "tests_total": len(checks),
                "goal_progress": float(checks["over_discount"]),
            },
        )
        self.receipts[receipt] = result
        return result
