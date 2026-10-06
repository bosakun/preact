import pytest

from preact.core.interfaces import EngineFailure
from preact.core.models import Estimate, FutureOutcome, Policy, PredictionRequest, State
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.physical import PhysicalWorld
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


class BranchingVerifier(LocalVerifier):
    """Conformance fixture; executable software claims are still real probe results."""

    async def predict(self, request):
        result = await super().predict(request)
        if not result.violations and not self.world.complete(result.successor):
            state = result.successor
            alternative = State.create(
                state.domain,
                {**state.payload, "belief_branch": "alternative"},
                "conformance-fixture",
                kind="hypothetical",
                parent_id=request.state.id,
            )
            result.outcomes = [
                FutureOutcome(label="Expected state", state=state, probability=Estimate(value=0.5)),
                FutureOutcome(
                    label="Alternative state", state=alternative, probability=Estimate(value=0.5)
                ),
            ]
            result.successor = None
        return result


async def test_branching_outcomes_progressively_expand_without_mutating_authority(tmp_path):
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")
    original = (await world.observe()).id
    runtime = Runtime(
        store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world), BranchingVerifier(world)])
    )
    result = await runtime.run(world)
    assert result["success"]
    events = store.read_events(runtime.run_id)
    nodes = [e["data"]["node"] for e in events if e["kind"] == "node"]
    assert any(n["kind"] == "outcome" for n in nodes)
    assert all(n["state"]["kind"] == "hypothetical" for n in nodes if n["kind"] == "outcome")
    first_observation = next(e for e in events if e["kind"] == "observed")
    assert first_observation["data"]["state"]["id"] == original
    widened = [e for e in events if e["kind"] == "search_widened"]
    assert widened[0]["data"]["released"] == 1
    assert any(e["data"]["released"] > 1 for e in widened)


async def test_bad_outcome_distribution_is_rejected():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]

    class BadWeights(BranchingVerifier):
        async def predict(self, request):
            result = await super().predict(request)
            for o in result.outcomes:
                o.probability.value = 0.8
            return result

    with pytest.raises(EngineFailure):
        await Registry([]).predict(
            BadWeights(world), PredictionRequest(state=state, actions=[action])
        )


async def test_real_physical_multistep_search_changes_first_action(tmp_path):
    choices = []
    for enabled in [False, True]:
        world, store = PhysicalWorld(), Store("sqlite:///:memory:")
        runtime = Runtime(
            store,
            Artifacts(str(tmp_path)),
            Registry([LocalHeuristic(world), LocalVerifier(world)]),
            Policy(search=enabled),
        )
        result = await runtime.run(world)
        assert result["success"] and not result["unsafe"]
        selected = [
            e
            for e in store.read_events(runtime.run_id)
            if e["kind"] == "decision" and e["data"]["decision"] == "execute"
        ]
        choices.append(selected[0]["data"]["action"]["name"])
    assert choices == ["Route around the obstacle", "Lift before transferring"]


async def test_node_budget_bounds_work_and_stale_state_never_executes(tmp_path):
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")
    runtime = Runtime(
        store,
        Artifacts(str(tmp_path)),
        Registry([LocalHeuristic(world), LocalVerifier(world)]),
        Policy(max_nodes=1),
    )
    result = await runtime.run(world)
    assert result["steps"] == 0
    assert len([n for n in runtime.nodes if n.action]) <= 1

    class ChangingWorld(SoftwareWorld):
        observations = 0

        async def observe(self):
            self.observations += 1
            if self.observations == 2:
                self.payload["stage"] = "external-change"
            return await super().observe()

    world, store = ChangingWorld(), Store("sqlite:///:memory:")
    runtime = Runtime(
        store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world), LocalVerifier(world)])
    )
    result = await runtime.run(world)
    assert result["steps"] == 0 and result["status"] == "abstained"
    assert any("State changed" in str(e["data"]) for e in store.read_events(runtime.run_id))
