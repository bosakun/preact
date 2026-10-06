"""Authority boundary violations must fail before acting or claiming an outcome."""

import pytest

from preact.core.models import State
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SHORTCUT, SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


@pytest.mark.parametrize("fault", ["hypothetical", "domain", "content"])
async def test_invalid_observer_cannot_commit_an_action(tmp_path, fault):
    class InvalidObserver(SoftwareWorld):
        executions = 0

        async def observe(self):
            state = await super().observe()
            if fault == "hypothetical":
                return state.model_copy(update={"kind": "hypothetical"})
            if fault == "domain":
                return State.create("physical", state.payload, "wrong-authority")
            state.payload["stage"] = "tampered after identity"
            return state

        async def execute(self, action, receipt):
            self.executions += 1
            return await super().execute(action, receipt)

    world, store = InvalidObserver(), Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([]))
    with pytest.raises(ValueError):
        await runtime.run(world, direct=True)
    assert world.executions == 0
    assert not store.pending_execution(runtime.run_id)


async def test_adapter_cannot_change_the_action_after_its_future_was_verified(tmp_path):
    class MutatingAdapter(SoftwareWorld):
        proposals = None

        async def propose(self, state, width):
            result = await super().propose(state, width)
            self.proposals = (self.proposals or []) + result
            return result

        async def observe(self):
            state = await super().observe()
            if self.proposals:
                harmful = {"files": {"checkout.py": SHORTCUT}, "stage": "shortcut"}
                for action in self.proposals:
                    action.payload = harmful
            return state

    world, store = MutatingAdapter(), Store("sqlite:///:memory:")
    runtime = Runtime(
        store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world), LocalVerifier(world)])
    )
    result = await runtime.run(world)
    assert result["success"] and not result["unsafe"]


@pytest.mark.parametrize("fault", ["state", "constraints"])
async def test_proposer_cannot_mutate_authoritative_input_or_weaken_task(tmp_path, fault):
    class InvalidProposer(SoftwareWorld):
        async def propose(self, state, width):
            actions = await super().propose(state, width)
            if fault == "state":
                state.payload["stage"] = "corrupted"
            else:
                self.task.required_checks.clear()
            return actions

    world, store = InvalidProposer(), Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([]))
    with pytest.raises(ValueError):
        await runtime.run(world, direct=True)
    assert not store.pending_execution(runtime.run_id)
    assert not store.error_rows()


async def test_authorization_expiring_during_durable_intent_never_dispatches(tmp_path, monkeypatch):
    import time

    from preact.core.decision import gate as original_gate

    class SlowIntent(Store):
        def intent(self, *args):
            receipt = super().intent(*args)
            time.sleep(0.05)
            return receipt

    class Counted(SoftwareWorld):
        executions = 0

        async def execute(self, action, receipt):
            self.executions += 1
            return await super().execute(action, receipt)

    def short_gate(*args):
        decision = original_gate(*args)
        decision.expires_at = time.time() + 0.01
        return decision

    monkeypatch.setattr("preact.core.runtime.gate", short_gate)
    world, store = Counted(), SlowIntent("sqlite:///:memory:")
    runtime = Runtime(
        store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world), LocalVerifier(world)])
    )
    result = await runtime.run(world)
    assert world.executions == 0
    assert not result["success"]
    assert result["status"] == "abstained"
    assert not store.pending_execution(runtime.run_id)
    events = store.read_events(runtime.run_id)
    assert any(e["kind"] == "execution_aborted" for e in events)
    assert not any(e["kind"] == "outcome" for e in events)


def test_known_aborted_intent_cannot_receive_a_late_observation():
    store = Store("sqlite:///:memory:")
    run_id = store.create_run({})
    receipt = store.intent(run_id, "state", "action")
    store.abort_execution(receipt, "Expired before dispatch")
    assert not store.pending_execution(run_id)
    with pytest.raises(RuntimeError, match="Only pending"):
        store.complete_execution(receipt, {"success": True})
    assert not store.error_rows()
