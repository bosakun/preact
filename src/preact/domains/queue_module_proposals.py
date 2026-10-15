"""Compose real LLM proposals with the existing guarded Queue prediction adapter.

The language model only ranks domain-owned candidates. Numerical prediction,
qualification, final selection, authorization and execution remain independent.
"""

import copy

from preact.cognition.deliberation import DeliberationPlanner
from preact.cognition.lifecycle import RoundBinding
from preact.cognition.selection import SelectionWorld
from preact.core.models import identity
from preact.domains.queue_learning import QueueLearningBackend
from preact.engines.reasoning import ModelProposer


class _CandidatePool:
    proposal_usage_complete = True

    def __init__(self, task, candidates):
        self.task = task.model_copy(deep=True)
        self.candidates = candidates

    async def propose(self, state, width):
        if any(a.state_id != state.id for a in self.candidates):
            raise ValueError("Candidate pool observation changed")
        return [a.model_copy(deep=True) for a in self.candidates[:width]]


class _CallRecord:
    """Retain the actual structured LLM output, without changing its computation."""

    def __init__(self, engine):
        self.engine, self.output = engine, None
        self.capabilities = engine.capabilities

    async def json_call(self, prompt, schema, seed, deadline):
        result, provenance = await self.engine.json_call(prompt, schema, seed, deadline)
        self.output = copy.deepcopy(result)
        return result, provenance


class QueueModuleProposals:
    """PredictionAdapter composition; never a second planner or an executor."""

    def __init__(self, source, task, reasoner):
        self.source, self.task, self.reasoner = source, task.model_copy(deep=True), reasoner
        self.store, self.usage = source.store, []
        self.max_calls = source.max_calls + 1
        self.proposal = None

    def signature(self):
        return identity(
            {
                "adapter": "queue-llm-proposals/v1",
                "source": self.source.signature(),
                "engine": self.reasoner.capabilities.model_dump(),
                "candidate_limit": 2,
                "task": self.task.model_dump(),
            }
        )

    async def heads(self, runs):
        return await self.source.heads(runs)

    async def candidates(self, state, width):
        self.proposal = None
        pool = await self.source.candidates(state.model_copy(deep=True), 3)
        call = _CallRecord(self.reasoner)
        proposer = ModelProposer(_CandidatePool(self.task, pool), call)
        try:
            candidates = await proposer.propose(state.model_copy(deep=True), min(width, 2))
        finally:
            self.usage.extend(copy.deepcopy(proposer.usage))
        self.proposal = {
            "adapter": "queue-llm-proposals/v1",
            "implementation": "preact.engines.reasoning.ModelProposer",
            "contract": "CandidateGenerator.propose",
            "state_id": state.id,
            "engine_id": self.reasoner.capabilities.engine_id,
            "engine_version": self.reasoner.capabilities.version,
            "called": bool(proposer.usage),
            "skip_reason": "single_readiness_candidate" if not proposer.usage else None,
            "input_candidates": [a.model_dump() for a in pool],
            "output_fingerprints": [a.fingerprint for a in candidates],
            "structured_output": call.output,
            "usage": copy.deepcopy(proposer.usage),
            "authority": "Proposal only; independent forecast, selection and Runtime required",
        }
        return candidates

    async def forecast(self, state, actions, runs):
        if self.proposal is None or self.proposal["output_fingerprints"] != [
            a.fingerprint for a in actions
        ]:
            raise ValueError("Proposal/forecast binding changed")
        try:
            forecast = await self.source.forecast(state, actions, runs)
        finally:
            self.usage.extend(self.source.usage)
            self.source.usage.clear()
        return {**forecast, "proposal_module": copy.deepcopy(self.proposal)}

    async def check_basis(self, record):
        if record["simulation"].get("proposal_module") != self.proposal:
            raise ValueError("Durable proposal module changed")
        await self.source.check_basis(record)


class QueueModuleLearningBackend(QueueLearningBackend):
    """Reuse the complete learning lifecycle, replacing only the proposal binding."""

    def __init__(self, lifecycle, evaluation_factory, reasoner):
        super().__init__(lifecycle, evaluation_factory)
        self.reasoner = reasoner

    def binding(self, world, checkpoint):
        native = super().binding(world, checkpoint)
        source = QueueModuleProposals(native.planner.source, world.task, self.reasoner)
        planner = DeliberationPlanner(
            world,
            source,
            native.planner.value,
            prefix_runs=checkpoint["monitor_runs"],
        )
        return RoundBinding(
            planner,
            native.registry,
            SelectionWorld(world, planner),
            planner,
            {
                **native.provenance,
                "proposal_implementation": {
                    "kind": "local_llm",
                    "engine_id": self.reasoner.capabilities.engine_id,
                    "engine_version": self.reasoner.capabilities.version,
                },
            },
        )
