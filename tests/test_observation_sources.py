import pytest

from preact.core.evidence import observation_findings
from preact.core.interfaces import EngineFailure
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier
from tests.fixtures.consequences import QueueEngine, QueueWorld


async def test_observation_sources_are_actual_receipt_bound_and_never_label_long_horizons(tmp_path):
    world, store = QueueWorld(), Store("sqlite:///:memory:")
    runtime = Runtime(
        store, Artifacts(str(tmp_path)), Registry([QueueEngine(world), QueueEngine(world, True)])
    )
    await runtime.run(world)
    events = store.read_events(runtime.run_id)
    evidence = [e for e in events if e["kind"] == "observation_evidence"]
    assert len(evidence) == 1
    findings = evidence[0]["data"]["findings"]
    assert all(
        f["source_kind"] == "observation" and f["evidence"] == "observation" for f in findings
    )
    assert all(f["result"]["claim"]["horizon"] == 1 for f in findings)
    outcome = next(e for e in events if e["kind"] == "outcome")
    assert all(f["source_reference"] == f"{runtime.run_id}:{outcome['seq']}" for f in findings)
    assert all(f["provenance"]["receipt"] == evidence[0]["data"]["receipt"] for f in findings)
    selected = next(n for n in runtime.nodes if n.actual)
    long_ids = {p.id for n in runtime.nodes for p in n.predictions if p.horizon > 1}
    assert not long_ids.intersection(row["prediction_id"] for row in store.error_rows())
    assert not any(
        e["data"].get("prediction_id") in long_ids for e in events if e["kind"] == "comparison"
    )
    assert all(n.actual is None for n in runtime.nodes if n is not selected)


async def test_pending_execution_and_different_action_cannot_receive_observation_evidence():
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")
    state = await world.observe()
    action, other = await world.propose(state, 2)
    run_id = store.create_run({})
    receipt = store.intent(run_id, state.id, action.fingerprint)
    observed = await world.execute(action, receipt)
    with pytest.raises(EngineFailure):
        observation_findings(
            observed,
            execution=store.execution_record(receipt),
            action=action,
            task=world.task,
            source_reference="outcome",
        )
    store.complete_execution(receipt, observed.model_dump())
    with pytest.raises(EngineFailure):
        observation_findings(
            observed,
            execution=store.execution_record(receipt),
            action=other,
            task=world.task,
            source_reference="outcome",
        )


@pytest.mark.parametrize("fault", ["evidence", "required-claim", "policy", "source", "state"])
async def test_authorization_tampering_after_durable_intent_prevents_dispatch(tmp_path, fault):
    class MutatingStore(Store):
        def intent(self, *args):
            receipt = super().intent(*args)
            node = next(n for n in runtime.nodes if n.status == "selected")
            if fault == "evidence":
                node.evaluation.evidence_ids.clear()
            elif fault == "required-claim":
                node.evaluation.claim_assessments.clear()
            elif fault == "source":
                node.predictions[0].raw["tampered_after_intent"] = True
            elif fault == "state":
                node.state.payload["tampered_after_intent"] = True
            else:
                runtime.policy.max_risk = 1
            return receipt

    world, store = SoftwareWorld(), MutatingStore("sqlite:///:memory:")
    runtime = Runtime(
        store,
        Artifacts(str(tmp_path)),
        Registry([LocalHeuristic(world), LocalVerifier(world)]),
        Policy(search=False),
    )
    result = await runtime.run(world)
    assert result["steps"] == 0 and result["status"] == "abstained"
    assert not store.pending_execution(runtime.run_id) and not store.error_rows()
    assert any(e["kind"] == "execution_aborted" for e in store.read_events(runtime.run_id))
