"""Exercise actual Queue/Agent/Runtime sessions and the public exhibition boundary."""

import asyncio

import httpx
import pytest
from sqlalchemy import update

from preact.core.models import Policy
from preact.core.store import Artifacts, Store
from preact.domains.queue_playground import collect, queue_world
from preact.playground.evidence import read_sources
from preact.service.app import create_app


@pytest.fixture
async def exhibition(tmp_path):
    app = create_app(
        "sqlite:///" + str(tmp_path / "ledger.db"),
        str(tmp_path / "artifacts"),
        "local",
        playground_root=tmp_path / "sessions",
        playground_recordings=tmp_path / "recordings",
        kiosk=True,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield app, client


async def until(client, identifier, predicate):
    async with asyncio.timeout(45):
        while True:
            response = await client.get(f"/api/playground/sessions/{identifier}")
            assert response.status_code == 200, response.text
            view = response.json()
            if predicate(view):
                return view
            assert view["session"]["status"] != "failed", view["session"]
            await asyncio.sleep(0.03)


async def start(client, scenario="stable_high"):
    response = await client.post("/api/playground/sessions", json={"scenario": scenario})
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def control(client, identifier, command, revision):
    return await client.post(
        f"/api/playground/sessions/{identifier}/control",
        json={"command": command, "revision": revision},
    )


async def test_live_step_pause_cas_receipts_and_learning_use_one_world(exhibition):
    app, client = exhibition
    identifier = await start(client)
    first = await until(client, identifier, lambda v: v["session"]["status"] == "paused")
    assert first["session"]["rounds"] == 1 and first["session"]["mode"] == "live"
    requests = await asyncio.gather(*[control(client, identifier, "step", 0) for _ in range(4)])
    assert sorted(r.status_code for r in requests) == [200, 409, 409, 409]
    second = await until(
        client,
        identifier,
        lambda v: v["session"]["status"] == "paused" and v["session"]["rounds"] == 2,
    )
    assert second["session"]["revision"] == 1
    await asyncio.sleep(0.08)
    assert (await client.get(f"/api/playground/sessions/{identifier}")).json()["session"][
        "rounds"
    ] == 2
    events = [e["event"] for e in second["events"]]
    outcomes = [e for e in second["events"] if e["event"]["kind"] == "outcome"]
    assert len(outcomes) == 2 and all(e["receipt_verified"] for e in outcomes)
    assert [e["event"]["data"]["observation"]["state"]["payload"]["tick"] for e in outcomes] == [
        1,
        2,
    ]
    for e in outcomes:
        receipt = e["event"]["data"]["observation"]["receipt"]
        assert app.state.store.execution_record(receipt)["status"] == "complete"
    assert sum(e["kind"] == "deliberation" for e in events) == 2
    assert sum(e["kind"] == "learning_experience" for e in events) == 2
    assert not any(e["kind"] == "learning_explicit_promotion" for e in events)
    assert (await control(client, identifier, "stop", 1)).status_code == 200
    stopped = await until(client, identifier, lambda v: v["session"]["status"] == "stopped")
    assert stopped["session"]["mode"] == "recorded"
    assert stopped["session"]["rounds"] == 2


async def test_auto_candidates_gate_and_actual_are_bound(exhibition):
    _, client = exhibition
    identifier = await start(client)
    await until(client, identifier, lambda v: v["session"]["status"] == "paused")
    assert (await control(client, identifier, "auto", 0)).status_code == 200
    await until(client, identifier, lambda v: v["session"]["rounds"] >= 11)
    assert (await control(client, identifier, "pause", 1)).status_code == 200
    view = await until(client, identifier, lambda v: v["session"]["status"] == "paused")
    runs = {}
    for e in view["events"]:
        runs.setdefault(e["event"]["run_id"], []).append(e["event"])
    found = False
    for events in runs.values():
        trace = next((e["data"] for e in events if e["kind"] == "deliberation"), None)
        if trace is None or len(trace["simulation"]["candidates"]) < 3:
            continue
        found = True
        choice = next(e["data"] for e in events if e["kind"] == "candidate_selection")
        candidate = next(
            f
            for f in trace["simulation"]["candidates"]
            if f["fingerprint"] == choice["action_fingerprint"]
        )
        actual = next(e["data"]["observation"] for e in events if e["kind"] == "outcome")
        intent = next(e["data"] for e in events if e["kind"] == "execution_intent")
        assert actual["receipt"] == intent["receipt"]
        assert all(r["prediction"]["successor"] is None for r in candidate["records"])
        assert all(
            r["prediction"]["state_id"] == trace["state"]["id"] for r in candidate["records"]
        )
        assert any(
            e["kind"] == "authorization" and e["data"]["decision"] == "execute" for e in events
        )
    assert found


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("stable_high", [3] * 9),
        ("stable_low", [1] * 9),
        ("high_to_low", [3] * 8 + [1]),
        ("low_to_high", [1] * 8 + [3]),
    ],
)
async def test_constructor_scenarios_reach_formal_measurements(tmp_path, scenario, expected):
    store = Store("sqlite:///" + str(tmp_path / "world.db"))
    world = queue_world(scenario, 11)
    before = await world.observe()
    assert "scenario" not in before.payload and "measured_service" not in before.payload
    assert "_service" not in before.payload
    runs = await collect(world, ["probe"] * 9, store, Artifacts(str(tmp_path / "artifacts")))
    stream = await read_sources(store, dict.fromkeys(runs, "world"))
    outcomes = [e.event.data["observation"] for e in stream if e.event.kind == "outcome"]
    assert [o["metrics"]["measured_service"] for o in outcomes] == expected
    assert [o["metrics"]["measurement_tick"] for o in outcomes] == list(range(9))
    assert all("measured_service" not in o["state"]["payload"] for o in outcomes)


async def test_kiosk_blocks_generic_execution_and_unapproved_parameters(exhibition):
    _, client = exhibition
    for path in ("/api/runs", "/api/ledger", "/api/artifacts/" + "a" * 64, "/openapi.json"):
        assert (await client.get(path)).status_code == 404
    assert (await client.post("/api/runs", json={"domain": "software"})).status_code == 404
    for body in (
        {"scenario": "unknown"},
        {"seed": -1},
        {"_service": [3]},
        {"policy": {"max_risk": 1}},
        {"promotion": "forged"},
        {"payload": {"queue": 9}},
    ):
        assert (await client.post("/api/playground/sessions", json=body)).status_code == 422
    assert (await client.get("/api/health")).json()["kiosk"]
    identifier = await start(client)
    assert (await client.post("/api/playground/sessions", json={})).status_code == 409
    assert (
        await client.post(f"/api/playground/sessions/{identifier}/promote", json={})
    ).status_code == 404


async def test_history_survives_restart_without_redispatch(exhibition, tmp_path):
    app, client = exhibition
    identifier = await start(client)
    first = await until(client, identifier, lambda v: v["session"]["status"] == "paused")
    await app.state.playground.close()
    again = create_app(
        "sqlite:///" + str(tmp_path / "ledger.db"),
        str(tmp_path / "artifacts"),
        "local",
        playground_root=tmp_path / "sessions",
        playground_recordings=tmp_path / "recordings",
    )
    async with again.router.lifespan_context(again):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=again), base_url="http://test"
        ) as other:
            saved = (await other.get(f"/api/playground/sessions/{identifier}")).json()
            assert saved["session"]["status"] == "interrupted"
            assert saved["session"]["mode"] == "recorded"
            assert [e["reference"] for e in saved["events"]] == [
                e["reference"] for e in first["events"]
            ]
            assert (await control(other, identifier, "step", 0)).status_code == 409
            await start(other, "stable_low")


async def test_receipt_tampering_is_not_presented_as_actual(exhibition):
    app, client = exhibition
    identifier = await start(client)
    view = await until(client, identifier, lambda v: v["session"]["status"] == "paused")
    outcome = next(e for e in view["events"] if e["event"]["kind"] == "outcome")
    receipt = outcome["event"]["data"]["observation"]["receipt"]
    store = app.state.store
    with store.db.begin() as connection:
        connection.execute(
            update(store.executions)
            .where(store.executions.c.id == receipt)
            .values(status="pending", receipt={})
        )
    with pytest.raises(ValueError, match="committed execution receipt"):
        await app.state.playground.snapshot(identifier)


async def test_delta_references_detect_stale_cursor(exhibition):
    _, client = exhibition
    identifier = await start(client)
    view = await until(client, identifier, lambda v: v["session"]["status"] == "paused")
    last = view["events"][-1]
    valid = await client.get(
        f"/api/playground/sessions/{identifier}",
        params={"after": last["index"], "reference": last["reference"]},
    )
    assert valid.json()["events"] == [] and not valid.json()["reset"]
    stale = await client.get(f"/api/playground/sessions/{identifier}?after=1&reference=wrong")
    assert stale.json()["reset"] and stale.json()["events"] == view["events"]


async def test_pause_during_verification_finishes_only_the_admitted_round(exhibition, monkeypatch):
    from preact.engines.information_queue import InformationBoundVerifier

    _, client = exhibition
    reached, release = asyncio.Event(), asyncio.Event()
    original = InformationBoundVerifier.predict

    async def bounded_pause(self, request):
        prediction = await original(self, request)
        if request.state.payload["episode_ticks"] == 59 and request.state.payload["tick"] == 0:
            reached.set()
            await release.wait()
        return prediction

    monkeypatch.setattr(InformationBoundVerifier, "predict", bounded_pause)
    identifier = await start(client)
    await asyncio.wait_for(reached.wait(), 15)
    assert (await control(client, identifier, "pause", 0)).status_code == 200
    release.set()
    view = await until(client, identifier, lambda v: v["session"]["status"] == "paused")
    assert view["session"]["rounds"] == 1
    assert sum(e["event"]["kind"] == "outcome" for e in view["events"]) == 1
    await asyncio.sleep(0.05)
    assert (await client.get(f"/api/playground/sessions/{identifier}")).json()["session"][
        "rounds"
    ] == 1


async def test_stop_during_preparation_never_starts_the_source_world(exhibition, monkeypatch):
    import preact.service.playground as service

    app, client = exhibition
    ready, release = asyncio.Event(), asyncio.Event()
    original = service.prepare_queue

    async def held(*args):
        prepared = await original(*args)
        ready.set()
        await release.wait()
        return prepared

    monkeypatch.setattr(service, "prepare_queue", held)
    identifier = await start(client)
    await asyncio.wait_for(ready.wait(), 15)
    assert (await control(client, identifier, "stop", 0)).status_code == 200
    release.set()
    view = await until(client, identifier, lambda v: v["session"]["status"] == "stopped")
    assert view["session"]["rounds"] == 0
    assert not any(e["scope"] == "world" for e in view["events"])
    preparation = view["session"]["provenance"]["preparation"]["training"]
    assert sum(len(runs) for runs in preparation.values()) == 16
    assert all(
        app.state.store.get_run(run)["result"]["steps"] == 1
        for runs in preparation.values()
        for run in runs
    )


async def test_exhibition_keeps_server_policy_and_does_not_use_s3(tmp_path, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "must-not-be-contacted")
    monkeypatch.setenv("PREACT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    limits = Policy(max_calls=1, max_risk=0.01, min_success=0.9, calibration=True)
    app = create_app(
        "sqlite:///" + str(tmp_path / "ledger.db"),
        mode="local",
        service_policy=limits,
        playground_root=tmp_path / "sessions",
        playground_recordings=tmp_path / "recordings",
        kiosk=True,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            identifier = await start(client)
            view = await until(client, identifier, lambda v: v["session"]["status"] == "failed")
            assert view["session"]["rounds"] == 0
            runs = [r for r in app.state.store.list_runs() if "policy" in r["config"]]
            assert len(runs) == 1
            policy = runs[0]["config"]["policy"]
            assert policy["max_calls"] == 1 and policy["max_risk"] == 0.01
            assert policy["min_success"] == 0.9 and policy["calibration"]
            assert not any(
                e["kind"] == "outcome" for e in app.state.store.read_events(runs[0]["id"])
            )


async def test_queue_journals_do_not_displace_legacy_world_history(tmp_path):
    from preact.cognition import CognitiveAgent, Goal, WorldPlanner
    from preact.core.registry import Registry
    from preact.datasets.programs import LOGIC
    from preact.domains.program import ProgramWorld
    from preact.engines.local import LocalHeuristic, LocalVerifier

    app = create_app(
        "sqlite:///" + str(tmp_path / "ledger.db"),
        str(tmp_path / "artifacts"),
        "local",
        playground_root=tmp_path / "sessions",
        playground_recordings=tmp_path / "recordings",
    )
    store = app.state.store
    expected = store.create_run({"request": {"domain": "software", "mode": "preact"}})
    world = ProgramWorld(LOGIC[0])
    result = await CognitiveAgent(
        store,
        Artifacts(str(tmp_path / "artifacts")),
        Registry([LocalHeuristic(world), LocalVerifier(world)]),
        WorldPlanner(world),
        [Goal(name="Repair", metric="goal_complete", target=1)],
        Policy(search=False, width=2, max_nodes=2, max_calls=64),
    ).run(world, 1)
    assert result.rounds[0]["steps"] == 1
    # Owned metadata-only fixtures: no execution or synthetic receipts are created.
    for _ in range(101):
        store.create_run({"kind": "cognitive_learning"})
    assert expected not in [r["id"] for r in store.list_runs()]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        records = (await client.get("/api/runs")).json()
        assert [r["id"] for r in records] == [*result.run_ids, expected]
