"""Shared structured-reasoning contracts; independent of the model serving vendor."""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import Field, ValidationError

from preact.core.interfaces import EngineFailure
from preact.core.models import Capabilities, Contract, Estimate, EvidenceKind, Prediction


class StructuredReasoner(Protocol):
    capabilities: Capabilities

    async def json_call(self, prompt: str, schema: dict, seed: int, deadline: float): ...


class ReasonedFuture(Contract):
    success: Estimate
    risk: Estimate
    assumptions: list[str] = Field(min_length=1)
    violations: list[str] = Field(default_factory=list)


async def predict_reasoned(engine, request):
    conditioned = request.model_dump(exclude={"deadline_seconds"})
    conditioned["state"] = request.state.model_dump(exclude={"timestamp"})
    conditioned["actions"] = [a.model_dump(exclude={"id"}) for a in request.actions]
    prompt = json.dumps(
        {
            "operation": "predict_action_postconditions",
            "definition": "success means this action preserves invariants and completes its local "
            "postconditions, not necessarily the entire task. Report residual uncertainty honestly.",
            "request": conditioned,
            "schema": ReasonedFuture.model_json_schema(),
        }
    )
    result, provenance = await engine.json_call(
        prompt, ReasonedFuture.model_json_schema(), request.seed, request.deadline_seconds
    )
    try:
        future = ReasonedFuture.model_validate(result)
    except ValidationError:
        raise EngineFailure("Invalid structured reasoning prediction schema") from None
    # A model cannot promote its own confidence into measurement authority.
    future.success.measured = future.risk.measured = False
    return Prediction(
        engine_id=engine.capabilities.engine_id,
        engine_version=engine.capabilities.version,
        family=engine.capabilities.family,
        state_id=request.state.id,
        action_ids=[a.id for a in request.actions],
        evidence=EvidenceKind.INFERENCE,
        success=future.success,
        risk=future.risk,
        assumptions=future.assumptions,
        violations=future.violations,
        cost_usd=provenance["cost_usd"],
        raw=provenance,
    )


class ModelProposer:
    """The same external agent supplies candidates for Direct and PreAct comparisons."""

    def __init__(self, world, engine: StructuredReasoner):
        self.world, self.engine, self.task = world, engine, world.task
        self.usage = []
        self.proposal_calls = 2 if getattr(world, "proposer", None) is not None else 1
        self.proposal_usage_complete = getattr(world, "proposal_usage_complete", False) is True

    def __getattr__(self, name):
        return getattr(self.world, name)

    async def propose(self, state, width):
        from preact.core.models import Action

        # Start with domain-valid candidates. Ranking is a real agent decision, shared
        # between the baseline and Core. Free-form code requires SandboxWorld.
        try:
            candidates = await self.world.propose(state, 5)
        finally:
            self.usage.extend(getattr(self.world, "usage", []) or [])
            if getattr(self.world, "usage", None):
                self.world.usage.clear()
        if len(candidates) < 2:
            # There is no ordering decision. Keep action validation and all later
            # prediction/measurement/authorization work; avoid an irrelevant request.
            return [Action.model_validate(a.model_dump()) for a in candidates[:width]]
        schema = {
            "type": "object",
            "properties": {
                "ranking": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": len(candidates) - 1},
                    "minItems": 1,
                    "maxItems": len(candidates),
                }
            },
            "required": ["ranking"],
            "additionalProperties": False,
        }
        data, provenance = await self.engine.json_call(
            json.dumps(
                {
                    "operation": "rank_actions",
                    "task": self.task.model_dump(),
                    "state": state.model_dump(exclude={"timestamp"}),
                    "candidates": [
                        {"index": i, "action": a.model_dump(exclude={"id"})}
                        for i, a in enumerate(candidates)
                    ],
                    "instruction": "Rank the supplied ZERO-BASED candidate indexes from best to worst based on the goal and invariants. Do not invent indexes or actions. Unranked candidates retain their input order.",
                }
            ),
            schema,
            self.task.seed,
            30,
        )
        self.usage.append(provenance)
        ranking = data["ranking"]
        if any(type(i) is not int or not 0 <= i < len(candidates) for i in ranking):
            raise EngineFailure("Invalid proposer ranking")
        indexes = list(dict.fromkeys(ranking + list(range(len(candidates)))))
        return [Action.model_validate(candidates[i].model_dump()) for i in indexes[:width]]
