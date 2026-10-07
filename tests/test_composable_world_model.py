import pytest

from preact.core.decision import evaluate
from preact.core.models import PredictionRequest
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.core.world_model import compose_world_model
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


async def test_composes_heterogeneous_forecast_and_measurement_without_false_independence():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    request = PredictionRequest(state=state, actions=[action])

    heuristic = await LocalHeuristic(world).predict(request)
    verifier = await LocalVerifier(world).predict(request)
    assessment = evaluate([heuristic, verifier], world.task, {})

    snapshot = compose_world_model(
        state,
        action,
        [heuristic, verifier],
        assessment,
        world.task,
    )

    assert snapshot.domain == "software"
    assert set(snapshot.evidence_kinds) == {"inference", "executable"}
    assert snapshot.families == ["execution", "local-heuristic"]
    assert snapshot.correlated_families == []
    assert snapshot.measured_families == ["execution"]
    assert {future.state.id for future in snapshot.futures} == {
        heuristic.successor.id,
        verifier.successor.id,
    }
    assert snapshot.unresolved_checks == []
    contributions = {item.engine_id: item for item in snapshot.contributions}
    assert contributions["local-heuristic"].roles == ["forecast", "successor_model"]
    assert contributions["local-tests"].roles == [
        "forecast",
        "measurement",
        "successor_model",
    ]


async def test_repeated_family_is_reported_as_correlated_provenance_not_extra_authority():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    request = PredictionRequest(state=state, actions=[action])
    first = await LocalHeuristic(world).predict(request)
    second = first.model_copy(deep=True)
    second.id = "same-family-second-sample"

    assessment = evaluate([first, second], world.task, {})
    snapshot = compose_world_model(state, action, [first, second], assessment, world.task)

    assert snapshot.families == ["local-heuristic"]
    assert snapshot.correlated_families == ["local-heuristic"]
    assert snapshot.measured_families == []
    assert snapshot.evaluation.success == pytest.approx(first.success.value)


async def test_runtime_emits_composed_world_model_snapshots(tmp_path):
    world = SoftwareWorld()
    store = Store("sqlite:///:memory:")
    runtime = Runtime(
        store,
        Artifacts(str(tmp_path)),
        Registry([LocalHeuristic(world), LocalVerifier(world)]),
    )

    result = await runtime.run(world)

    assert result["success"] and not result["unsafe"]
    snapshots = [
        event["data"]["world_model"]
        for event in store.read_events(runtime.run_id)
        if event["kind"] == "world_model_snapshot"
    ]
    assert snapshots
    assert all(snapshot["domain"] == "software" for snapshot in snapshots)
    assert any(
        {"inference", "executable"}.issubset(set(snapshot["evidence_kinds"]))
        for snapshot in snapshots
    )
    assert all("evaluation" in snapshot for snapshot in snapshots)
