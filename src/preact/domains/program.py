"""Bounded real program-repair tasks behind the same World and FutureEngine contracts."""

import ast
import asyncio
import copy
import difflib
import json
import os
import secrets
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from preact.core.models import Action, Observation, State, Task, identity
from preact.datasets.programs import Program


class ProgramWorld:
    def __init__(self, spec: Program, seed=0, evaluation_seed=None):
        self.spec = spec
        self.task = Task(
            id=spec.name,
            domain="software",
            title=spec.name.replace("_", " "),
            goal=spec.contract,
            seed=seed,
            max_steps=2,
            required_checks=["syntax", "invariants"],
            metric_scales={"test_pass_fraction": 1},
        )
        self.payload = {
            "files": {"program.py": spec.source(spec.initial)},
            "task_title": self.task.title,
            "public_examples": [
                {"args": list(args), "expected": spec.oracle(*args)} for args in spec.examples[:2]
            ],
            "stage": "initial",
            "goal_complete": False,
        }
        self.receipts = {}
        self.evaluation_seed = evaluation_seed
        self.evaluation_counter = 0

    def next_evaluation_seed(self):
        self.evaluation_counter += 1
        if self.evaluation_seed is None:
            return secrets.randbits(32)
        return int(
            identity({"seed": self.evaluation_seed, "request": self.evaluation_counter})[:8], 16
        )

    async def observe(self):
        return State.create(
            "software", copy.deepcopy(self.payload), "trusted-program:" + self.spec.family
        )

    async def propose(self, state, width):
        # No oracle calls or candidate test execution: the local Direct agent is an
        # explicit minimum-AST-edit proposer, not a disguised verifier/model.
        current = ast.dump(ast.parse(state.payload["files"]["program.py"]))

        def score(source):
            distance = (
                1 - difflib.SequenceMatcher(None, current, ast.dump(ast.parse(source))).ratio()
            )
            tie = identity({"source": source, "seed": self.task.seed})
            return distance, tie

        sources = sorted(
            [s for s in self.spec.candidates if s != state.payload["files"]["program.py"]],
            key=score,
        )
        return [
            Action(
                name="Candidate patch " + identity(source)[:6],
                kind="patch",
                state_id=state.id,
                payload={"files": {"program.py": source}},
                rationale="Candidate for the public function contract; local ranking minimizes AST edit distance",
            )
            for source in sources[:width]
        ]

    def validate(self, state, action):
        if state.domain != "software" or action.state_id != state.id or action.kind != "patch":
            raise ValueError("Invalid program state/action")
        replacement = action.payload.get("files", {})
        if (
            set(replacement) != {"program.py"}
            or replacement["program.py"] not in self.spec.candidates
        ):
            raise ValueError(
                "Local program execution accepts only enumerated first-party candidates"
            )
        if state.payload["files"]["program.py"] not in {
            *self.spec.candidates,
            self.spec.source(self.spec.initial),
        }:
            raise ValueError("Untrusted code requires an admitted remote sandbox")

    def materialize(self, state, action):
        self.validate(state, action)
        payload = {
            **copy.deepcopy(state.payload),
            "files": copy.deepcopy(action.payload["files"]),
            "stage": "patched",
            "goal_complete": False,
        }
        return State.create(
            "software",
            payload,
            "program-patch-materialization",
            kind="hypothetical",
            parent_id=state.id,
        )

    def protected_request(self, seed):
        return {"target": self.spec.name, "cases": self.spec.cases(seed)}

    async def measure(self, state, action, seed):
        successor = self.materialize(state, action)
        oracle = self.protected_request(seed)
        with tempfile.TemporaryDirectory(prefix="preact-program-") as directory:
            root = Path(directory)
            (root / "program.py").write_text(successor.payload["files"]["program.py"])
            (root / "oracle.json").write_text(json.dumps(oracle))
            (root / "probe.py").write_bytes(
                files("preact.domains").joinpath("program_probe.py").read_bytes()
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(root / "probe.py"),
                str(root / "program.py"),
                str(root / "oracle.json"),
                cwd=directory,
                env={"PATH": os.defpath},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, _ = await asyncio.wait_for(process.communicate(), 5)
            except (TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.wait()
                raise
            if process.returncode:
                raise RuntimeError("Protected program evaluation failed")
            measured = json.loads(out)
        payload = {**successor.payload, "goal_complete": measured["goal_complete"]}
        return (
            payload,
            measured["checks"],
            measured["metrics"],
            {
                **measured["evidence"],
                "oracle_hash": identity(oracle),
                "scope": "Executed bounded first-party Python; no sponsor validation",
            },
        )

    async def verify_future(self, state, action, seed):
        # Independent fixture seed for every request; reproducible benchmark seeds
        # condition the tasks/proposer, not the hidden evaluator's observed labels.
        return await self.measure(state, action, self.next_evaluation_seed())

    def complete(self, state):
        return bool(state.payload.get("goal_complete"))

    async def execute(self, action, receipt):
        if receipt in self.receipts:
            return self.receipts[receipt].model_copy(deep=True)
        payload, checks, metrics, _ = await self.measure(
            await self.observe(), action, self.next_evaluation_seed()
        )
        self.payload = copy.deepcopy(payload)
        safe = all(checks.values())
        observation = Observation(
            state=await self.observe(),
            success=self.complete(await self.observe()),
            unsafe=not safe,
            checks={**checks, "action_success": safe},
            metrics=metrics,
            receipt=receipt,
            cost_usd=0,
        )
        self.receipts[receipt] = observation.model_copy(deep=True)
        return observation.model_copy(deep=True)
