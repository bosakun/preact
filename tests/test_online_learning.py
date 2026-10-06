"""Fault injection with actual trusted execution outcomes, not fabricated ledger labels."""

from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier, certain


class FaultyVerifier(LocalVerifier):
    def __init__(self, world):
        super().__init__(world)
        self.capabilities = self.capabilities.model_copy(update={"engine_id": "fault-injection"})

    async def predict(self, request):
        result = await super().predict(request)
        result.engine_id = self.capabilities.engine_id
        # Explicit injected verifier bug: wrongly discard a real failed invariant.
        result.success, result.risk = certain(True), certain(False)
        result.violations = []
        result.mandatory_checks = {key: True for key in result.mandatory_checks}
        return result


class CorrectiveVerifier(LocalVerifier):
    def __init__(self, world):
        super().__init__(world)
        self.capabilities = self.capabilities.model_copy(
            update={
                "tier": 3,
                "refines_engine_ids": ["fault-injection"],
            }
        )

    async def predict(self, request):
        result = await super().predict(request)
        result.refines_engine_ids = ["fault-injection"]
        return result


async def test_actual_historical_errors_trigger_extra_verification_and_correct_choice(tmp_path):
    store, artifacts = Store("sqlite:///:memory:"), Artifacts(str(tmp_path))
    for _ in range(5):
        world = SoftwareWorld()
        world.task.max_steps = 1
        runtime = Runtime(store, artifacts, Registry([FaultyVerifier(world)]), Policy(search=False))
        result = await runtime.run(world)
        assert result["unsafe"]  # actual subprocess observes invariant failure
    assert len(store.error_rows()) == 5
    world = SoftwareWorld()
    runtime = Runtime(
        store, artifacts, Registry([FaultyVerifier(world), CorrectiveVerifier(world)])
    )
    result = await runtime.run(world)
    assert result["success"] and not result["unsafe"]
    events = store.read_events(runtime.run_id)
    assert any(e["kind"] == "escalation" for e in events)
    executed = [
        e["data"]["action"]["name"]
        for e in events
        if e["kind"] == "decision" and e["data"]["decision"] == "execute"
    ]
    assert executed[0] == "Prepare a reusable validator"
