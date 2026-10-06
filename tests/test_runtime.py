import importlib.util

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from preact.core.calibration import Calibration
from preact.core.decision import evaluate, gate
from preact.core.interfaces import EngineFailure
from preact.core.models import Decision, Estimate, Policy, PredictionRequest
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.physical import PhysicalWorld, robust_clearance
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


@given(
    st.floats(min_value=0, max_value=1, allow_nan=False),
    st.floats(min_value=0, max_value=1, allow_nan=False),
)
def test_estimate_rejects_inverted_bounds(a, b):
    if a > b:
        with pytest.raises(ValidationError):
            Estimate(lower=a, upper=b)
    else:
        assert Estimate(lower=a, upper=b).upper == b


async def test_unknown_and_reasoning_only_evidence_abstains():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 3))[1]
    prediction = await LocalHeuristic(world).predict(
        PredictionRequest(state=state, actions=[action])
    )
    assessment = evaluate([prediction], world.task, {})
    assert assessment.risk_upper == 1
    assert (
        gate(assessment, state.id, action.fingerprint, Policy(), True).decision == Decision.VERIFY
    )
    assert (
        gate(assessment, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN
    )


async def test_correlated_models_do_not_acquire_extra_votes():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 3))[0]
    req = PredictionRequest(state=state, actions=[action])
    cheap = await LocalHeuristic(world).predict(req)
    measured = await LocalVerifier(world).predict(req)
    first = evaluate([cheap, measured], world.task, {})
    repeated = evaluate([cheap] * 20 + [measured], world.task, {})
    assert first.success == pytest.approx(repeated.success)
    assert first.disagreement > 0.2
    assert gate(repeated, state.id, action.fingerprint, Policy(), True).decision == Decision.ABSTAIN


async def test_registry_rejects_forged_authority_and_lineage():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]

    class Forged(LocalHeuristic):
        async def predict(self, request):
            result = await super().predict(request)
            result.evidence = "executable"
            return result

    with pytest.raises(EngineFailure):
        await Registry([]).predict(Forged(world), PredictionRequest(state=state, actions=[action]))
    action.state_id = "stale-state"
    with pytest.raises(EngineFailure):
        await Registry([]).predict(
            LocalHeuristic(world), PredictionRequest(state=state, actions=[action])
        )


async def test_engine_mutation_cannot_corrupt_authority_or_its_declared_capabilities():
    from preact.domains.software import SHORTCUT

    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]

    class Mutating(LocalHeuristic):
        async def predict(self, request):
            request.state.payload["files"]["checkout.py"] = SHORTCUT
            return await super().predict(request)

    with pytest.raises(EngineFailure, match="mutated"):
        await Registry([]).predict(
            Mutating(world), PredictionRequest(state=state, actions=[action])
        )
    assert (await world.observe()).id == state.id

    class Promoting(LocalHeuristic):
        async def predict(self, request):
            self.capabilities.evidence = "simulation"
            result = await super().predict(request)
            result.evidence = "simulation"
            return result

    registry, engine = Registry([]), Promoting(world)
    with pytest.raises(EngineFailure, match="contract"):
        await registry.predict(engine, PredictionRequest(state=state, actions=[action]))
    with pytest.raises(EngineFailure, match="registered capabilities"):
        await registry.predict(engine, PredictionRequest(state=state, actions=[action]))


async def test_action_authorization_binds_duration():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    altered = action.model_copy(update={"duration": 2 * action.duration})
    assert action.fingerprint != altered.fingerprint


@pytest.mark.parametrize("factory", [SoftwareWorld, PhysicalWorld])
async def test_shared_core_rejects_unsafe_shortcuts_and_executes_multistep(factory, tmp_path):
    if factory == PhysicalWorld and not importlib.util.find_spec("mujoco"):
        pytest.skip("install physical extra for real simulation")
    world, store = factory(), Store("sqlite:///:memory:")
    runtime = Runtime(
        store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world), LocalVerifier(world)])
    )
    result = await runtime.run(world)
    assert result["success"] and not result["unsafe"] and result["steps"] >= 2
    events = store.read_events(runtime.run_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert any(e["kind"] == "node" and e["data"]["node"]["depth"] >= 2 for e in events)
    rejected = [
        e
        for e in events
        if e["kind"] == "gate_preview" and e["data"]["gate"]["decision"] == "abstain"
    ]
    assert rejected
    predicted = [e for e in events if e["kind"] == "prediction"]
    assert len(store.error_rows()) < len(predicted)  # unexecuted branches never become ground truth
    for e in [e for e in events if e["kind"] == "outcome"]:
        intents = [
            i
            for i in events
            if i["kind"] == "execution_intent" and i["data"]["node_id"] == e["data"]["node_id"]
        ]
        assert len(intents) == 1 and intents[0]["seq"] < e["seq"]


async def test_direct_baseline_really_executes_regression(tmp_path):
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([]))
    result = await runtime.run(world, direct=True)
    assert result["unsafe"] and not result["success"] and result["calls"] == 0


async def test_budget_exhaustion_abstains_without_side_effect(tmp_path):
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")
    runtime = Runtime(
        store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world)]), Policy(max_calls=1)
    )
    result = await runtime.run(world)
    assert result["status"] == "abstained" and result["steps"] == 0
    assert not any(e["kind"] == "execution_intent" for e in store.read_events(runtime.run_id))


async def test_model_proposal_budget_is_checked_before_any_external_call(tmp_path):
    class TwoCallProposer(SoftwareWorld):
        proposal_calls = 2
        invoked = False

        async def propose(self, state, width):
            self.invoked = True
            return await super().propose(state, width)

    world = TwoCallProposer()
    runtime = Runtime(
        Store("sqlite:///:memory:"), Artifacts(str(tmp_path)), Registry([]), Policy(max_calls=1)
    )
    result = await runtime.run(world)
    assert not world.invoked and result["steps"] == 0


async def test_measured_engine_drift_changes_gate_and_domain_labels_do_not_pool():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    prediction = await LocalVerifier(world).predict(
        PredictionRequest(state=state, actions=[action])
    )
    fresh = evaluate([prediction], world.task, {})
    assert gate(fresh, state.id, action.fingerprint, Policy(), True).decision == Decision.EXECUTE
    store = Store("sqlite:///:memory:")
    for i in range(8):
        store.record_error(
            str(i), "local-tests@1", "software:checkout-repair:h1", 1, False, 0, True
        )
    calibration = Calibration(store)
    drifted = evaluate(
        [prediction], world.task, calibration.snapshot("software:checkout-repair:h1")
    )
    assert gate(drifted, state.id, action.fingerprint, Policy(), True).decision == Decision.VERIFY
    assert calibration.snapshot("physical:cube-transfer:h1")["local-tests@1"]["n"] == 0


def test_ledger_is_idempotent_and_ignores_simulation_labels():
    store = Store("sqlite:///:memory:")
    assert store.record_error("one", "engine@1", "software:task:h1", 0.9, False, 0.1, True)
    assert not store.record_error("one", "engine@1", "software:task:h1", 0.9, False, 0.1, True)
    store.record_error(
        "imagined", "engine@1", "software:task:h1", 0.9, False, 0.1, True, "simulation"
    )
    stats = Calibration(store).snapshot("software:task:h1")["engine@1"]
    assert stats["n"] == 1
    assert Calibration.calibrate(0.9, stats) < 0.9


def test_restart_does_not_retry_pending_execution(tmp_path):
    url = "sqlite:///" + str(tmp_path / "durable.db")
    store = Store(url)
    run = store.create_run({})
    store.intent(run, "state", "action")
    restarted = Store(url)
    restarted.recover()
    assert restarted.get_run(run)["status"] == "interrupted"
    with pytest.raises(RuntimeError):
        restarted.intent(run, "state", "action")


def test_artifacts_reject_path_escape_and_corruption(tmp_path):
    artifacts = Artifacts(str(tmp_path))
    digest = artifacts.put(b"actual evidence")
    assert artifacts.read(digest) == b"actual evidence"
    with pytest.raises(ValueError):
        artifacts.read("../.env")
    (tmp_path / digest).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        artifacts.read(digest)


def test_swept_geometry_rejects_crossing_but_allows_lift():
    assert not robust_clearance([-0.3, 0, 0.04], [0.3, 0, 0.04], [0, 0, 0.1], [0.07, 0.1, 0.1])
    assert robust_clearance([-0.3, 0, 0.32], [0.3, 0, 0.32], [0, 0, 0.1], [0.07, 0.1, 0.1])
