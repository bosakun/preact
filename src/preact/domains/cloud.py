"""Cloud authority adapters: prediction and execution are distinct calls."""

import ast
import json
import os

import httpx
from pydantic import Field

from preact.core.interfaces import EngineFailure
from preact.core.models import Action, Contract, Observation, State
from preact.domains.physical import PhysicalWorld
from preact.domains.repository import RepositoryWorld
from preact.domains.software import SoftwareWorld


def validate_generated_patch(source):
    if not isinstance(source, str) or len(source) > 6000:
        raise ValueError("Patch exceeds the bounded repair contract")
    tree = ast.parse(source)
    permitted = {
        ast.Module,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.If,
        ast.Raise,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Constant,
        ast.Return,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Compare,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Eq,
        ast.NotEq,
        ast.BoolOp,
        ast.Or,
        ast.And,
        ast.UnaryOp,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.Assign,
        ast.Expr,
        ast.keyword,
        ast.Pass,
    }
    definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    functions = {n.name for n in definitions}
    approved_operations = {"ValueError", "max", "min", "abs", "round"}
    if "checkout" not in functions or any(not isinstance(n, ast.FunctionDef) for n in tree.body):
        raise ValueError("Repair module must contain only bounded function definitions")
    if len(functions) != len(definitions):
        raise ValueError("Repair function definitions must be unique")
    if any(name.startswith("__") for name in functions):
        raise ValueError("Reserved repair function identifiers are outside the task contract")
    if functions & approved_operations:
        raise ValueError("Approved operations cannot be replaced")
    call_targets = functions | approved_operations
    for node in ast.walk(tree):
        if type(node) not in permitted:
            raise ValueError(
                "Imports, attributes, loops and external effects are outside this task"
            )
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("Introspection is outside the task contract")
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            raise ValueError("Decorators are outside the task contract")
        if isinstance(node, ast.FunctionDef) and node not in definitions:
            raise ValueError("Repair functions must be declared at module scope")
        if isinstance(node, ast.arg) and node.arg in call_targets:
            raise ValueError("Parameters cannot shadow approved call targets")
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id in call_targets
        ):
            raise ValueError("Approved call targets cannot be rebound")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in call_targets
        ):
            raise ValueError("Unapproved function call")
    # This is task validation, not sandbox isolation. Every generated source still
    # executes only in remote ConTree, never in the local trusted-source runner.


class PatchCandidate(Contract):
    name: str = Field(max_length=100)
    source: str = Field(max_length=6000)
    rationale: str = Field(max_length=1000)


class PatchCandidates(Contract):
    candidates: list[PatchCandidate] = Field(min_length=1, max_length=5)


class SandboxWorld(SoftwareWorld):
    sandbox = None
    proposer = None
    usage = None
    proposal_usage_complete = True

    def __init__(self, seed=0):
        super().__init__(seed)
        self.usage = []

    async def propose(self, state, width):
        if not self.proposer:
            raise EngineFailure("Sandbox code generation requires a real model proposer")
        data, provenance = await self.proposer.json_call(
            json.dumps(
                {
                    "operation": "propose_repository_patches",
                    "task": self.task.model_dump(),
                    "state": state.model_dump(),
                    "max_candidates": width,
                    "instruction": "Propose diverse full replacements for checkout.py. Include cautious "
                    "preparatory refactors when useful. Keep module pure: functions, expressions, "
                    "conditionals, ValueError, min/max/abs/round only; no imports, attributes or I/O.",
                }
            ),
            PatchCandidates.model_json_schema(),
            self.task.seed,
            30,
        )
        self.usage.append(provenance)
        result = PatchCandidates.model_validate(data)
        actions = []
        for candidate in result.candidates[:width]:
            validate_generated_patch(candidate.source)
            actions.append(
                Action(
                    name=candidate.name,
                    kind="patch",
                    state_id=state.id,
                    payload={"files": {"checkout.py": candidate.source}, "stage": "model-patch"},
                    rationale=candidate.rationale,
                )
            )
        return actions

    def validate(self, state, action):
        if (
            action.state_id != state.id
            or action.kind != "patch"
            or set(action.payload.get("files", {})) != {"checkout.py"}
        ):
            raise ValueError("Invalid generated software action")
        validate_generated_patch(action.payload["files"]["checkout.py"])

    async def observe(self):
        return State.create("software", self.payload.copy(), "token-factory-episode")

    async def execute(self, action, receipt):
        if receipt in self.receipts:
            return self.receipts[receipt]
        state = await self.observe()
        successor = self.materialize(state, action)
        checks, provenance = await self.sandbox.measure(
            successor.payload["files"]["checkout.py"], parent=state
        )
        self.payload = successor.payload
        self.payload["goal_complete"] = all(checks.values())
        regressions = all(
            checks[k] for k in ["ordinary", "zero", "negative_total", "protected_invariants"]
        )
        self.payload["execution"] = provenance
        result = Observation(
            execution_calls=provenance["operation_count"],
            state=await self.observe(),
            success=self.complete(await self.observe()),
            unsafe=not regressions,
            checks={**checks, "action_success": regressions},
            metrics={"goal_progress": float(checks["over_discount"])},
            receipt=receipt,
        )
        self.receipts[receipt] = result
        return result


class SandboxRepositoryWorld(RepositoryWorld):
    """Same bounded release workflow with prediction and authority in ConTree."""

    release_workflow = True

    async def observe(self):
        import copy

        return State.create(
            "software", copy.deepcopy(self.payload), "token-factory-release-authority"
        )

    async def execute(self, action, receipt):
        if receipt in self.receipts:
            return self.receipts[receipt]
        result = await self.sandbox.measure_release(await self.observe(), action)
        self.payload = result["payload"]
        safe = all(result["checks"].values())
        observation = Observation(
            state=await self.observe(),
            success=self.complete(await self.observe()),
            unsafe=not safe,
            checks={**result["checks"], "action_success": safe},
            metrics=result["metrics"],
            receipt=receipt,
            execution_calls=result["provenance"]["operation_count"],
        )
        self.receipts[receipt] = observation
        return observation


class IsaacWorld(PhysicalWorld):
    @classmethod
    async def connect(cls, seed):
        endpoint = os.getenv("ISAAC_ENDPOINT")
        if not endpoint:
            raise EngineFailure("ISAAC_ENDPOINT is required for cloud Physical World")
        self = cls(seed)
        self.endpoint = endpoint.rstrip("/")
        self.client = httpx.AsyncClient(
            headers={"Authorization": "Bearer " + os.getenv("WORKER_TOKEN", "")}, timeout=60
        )
        try:
            response = await self.client.post(self.endpoint + "/episodes", json={"seed": seed})
            response.raise_for_status()
            self.episode = response.json()["id"]
            return self
        except BaseException:
            await self.client.aclose()
            raise

    async def aclose(self):
        await self.client.aclose()

    async def observe(self):
        response = await self.client.get(self.endpoint + f"/episodes/{self.episode}")
        response.raise_for_status()
        state = State.model_validate(response.json())
        if state.domain != "physical" or state.kind != "observed":
            raise EngineFailure("Invalid Isaac authority observation")
        return state

    async def execute(self, action, receipt):
        response = await self.client.post(
            self.endpoint + f"/episodes/{self.episode}/actions",
            json={"action": action.model_dump(), "receipt": receipt},
        )
        response.raise_for_status()
        result = Observation.model_validate(response.json())
        if result.receipt != receipt:
            raise EngineFailure("Isaac execution receipt mismatch")
        return result
