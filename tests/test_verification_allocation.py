"""Evidence allocation affects real local verification, with no live sponsor claims."""

from preact.core.decision import evaluate
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.core.verification import rank_verification
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


class PricedVerifier(LocalVerifier):
    def __init__(self, world, name, cost, latency=0.01):
        super().__init__(world)
        self.invocations = 0
        self.capabilities = self.capabilities.model_copy(
            update={
                "engine_id": name,
                "estimated_cost_usd": cost,
                "estimated_latency_seconds": latency,
                "verification_checks": world.task.required_checks,
            }
        )

    async def predict(self, request):
        self.invocations += 1
        prediction = await super().predict(request)
        prediction.engine_id = self.capabilities.engine_id
        return prediction


async def test_cost_allocation_uses_cheaper_sufficient_real_probes(tmp_path):
    world = SoftwareWorld()
    expensive = PricedVerifier(world, "a-expensive", 0.9)
    cheap = PricedVerifier(world, "z-cheap", 0.01)
    runtime = Runtime(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([LocalHeuristic(world), expensive, cheap]),
    )
    result = await runtime.run(world)
    assert result["success"] and not result["unsafe"]
    assert cheap.invocations > 0 and expensive.invocations == 0
    plans = [
        e["data"]["plans"]
        for e in runtime.store.read_events(runtime.run_id)
        if e["kind"] == "verification_allocation"
    ]
    assert plans and plans[0][0]["engine_id"] == "z-cheap"
    assert plans[0][0]["unresolved_checks_covered"] == sorted(world.task.required_checks)


async def test_unaffordable_or_too_slow_evidence_abstains_before_dispatch(tmp_path):
    world = SoftwareWorld()
    costly = PricedVerifier(world, "costly", 2)
    slow = PricedVerifier(world, "slow", 0, 60)
    store = Store("sqlite:///:memory:")
    runtime = Runtime(
        store,
        Artifacts(str(tmp_path)),
        Registry([LocalHeuristic(world), costly, slow]),
        Policy(search=False),
    )
    result = await runtime.run(world)
    assert result["status"] == "abstained" and result["steps"] == 0
    assert costly.invocations == slow.invocations == 0
    assert not any(e["kind"] == "execution_intent" for e in store.read_events(runtime.run_id))
    assert any(
        not p["admissible"]
        for e in store.read_events(runtime.run_id)
        if e["kind"] == "verification_allocation"
        for p in e["data"]["plans"]
    )


async def test_initial_engine_also_obeys_declared_resource_admission(tmp_path):
    world = SoftwareWorld()
    costly = PricedVerifier(world, "only-engine", 2)
    runtime = Runtime(Store("sqlite:///:memory:"), Artifacts(str(tmp_path)), Registry([costly]))
    result = await runtime.run(world)
    assert costly.invocations == 0 and result["status"] == "abstained"
    assert result["calls"] == 0 and result["steps"] == 0


def test_contextual_reliability_and_claim_coverage_change_evidence_priority():
    world = SoftwareWorld()
    partial = PricedVerifier(world, "a-partial", 0.01)
    partial.capabilities.verification_checks = []
    full = PricedVerifier(world, "z-full", 0.01)
    registry = Registry([partial, full])
    evaluation = evaluate([], world.task, {})
    budget = {"calls": 10, "seconds": 120, "cost_usd": 1}
    plans = rank_verification(
        registry.engines, registry.declarations, evaluation, world.task, {}, budget
    )
    assert plans[0][0] is full
    drift = {"z-full@1": {"weight": 0.1, "drift": True}}
    plans = rank_verification(
        registry.engines, registry.declarations, evaluation, world.task, drift, budget
    )
    assert plans[0][0] is partial
    assert plans[1][1]["contextual_weight"] == 0.05


async def test_fixed_condition_preserves_order_even_when_evidence_is_more_expensive(tmp_path):
    world = SoftwareWorld()
    expensive = PricedVerifier(world, "a-expensive", 0.9)
    cheap = PricedVerifier(world, "z-cheap", 0.01)
    runtime = Runtime(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([LocalHeuristic(world), expensive, cheap]),
        Policy(adaptive=False),
    )
    assert (await runtime.run(world))["success"]
    assert expensive.invocations > 0 and cheap.invocations > 0
