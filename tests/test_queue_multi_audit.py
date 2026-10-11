import copy
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import update

from preact.core.store import Artifacts, Store
from scripts.audit_queue_multi_selection import audit_case
from scripts.benchmark_queue_model_selection import fit_model
from scripts.benchmark_queue_multi_selection import episode


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def audit_fixture(tmp_path_factory):
    directory = tmp_path_factory.mktemp("multi-audit")
    store = Store("sqlite:///" + str(directory / "ledger.db"))
    artifacts = Artifacts(str(directory / "artifacts"))
    protocol = json.loads(Path("benchmarks/queue-multi-selection-v1.json").read_text())
    # A small integrity fixture, explicitly separate from the frozen main protocol.
    protocol["episode_ticks"] = 16
    model, training, digest = await fit_model(store, artifacts, protocol, True)
    case = await episode(
        store,
        artifacts,
        protocol,
        model,
        training,
        digest,
        "admission_veto",
        201,
        "multi_learned",
        {
            "high_first": True,
            "shift_tick": 16,
            "noise": 0,
            "fixture": True,
            "warmup": ["probe"] * 8,
        },
    )
    yield store, artifacts, protocol, case
    store.db.dispose()


async def test_independent_auditor_reconstructs_actual_choice_and_receipts(audit_fixture):
    store, artifacts, protocol, case = audit_fixture
    assert await audit_case(store, artifacts, protocol, case) == (8, 8)
    assert case["metrics"]["alternatives_executed"] == 7


@pytest.mark.parametrize("mutation", ["value", "qualified", "selected", "fresh", "metrics"])
async def test_independent_auditor_rejects_tampered_records(audit_fixture, mutation):
    store, artifacts, protocol, original = audit_fixture
    case = copy.deepcopy(original)
    if mutation == "metrics":
        case["metrics"]["alternatives_executed"] += 1
        with pytest.raises(ValueError, match="Metric"):
            await audit_case(store, artifacts, protocol, case)
        return
    kind = {
        "value": "cognitive_prediction",
        "qualified": "candidate_qualification",
        "selected": "candidate_selection",
        "fresh": "cognitive_multi_dispatch",
    }[mutation]
    event = next(e for e in store.read_events(case["runs"][1]) if e["kind"] == kind)
    data = copy.deepcopy(event["data"])
    if mutation == "value":
        data["candidates"][0]["value"] += 1
    elif mutation == "qualified":
        data["batch"]["candidates"][0]["status"] = "qualified"
    elif mutation == "selected":
        data["node_id"] = "forged-node"
    else:
        data["fresh_state"]["provenance"] = "forged"
    with store.db.begin() as connection:
        connection.execute(
            update(store.events)
            .where(
                (store.events.c.run_id == event["run_id"]) & (store.events.c.seq == event["seq"])
            )
            .values(data=data)
        )
    try:
        with pytest.raises(ValueError):
            await audit_case(store, artifacts, protocol, case)
    finally:
        with store.db.begin() as connection:
            connection.execute(
                update(store.events)
                .where(
                    (store.events.c.run_id == event["run_id"])
                    & (store.events.c.seq == event["seq"])
                )
                .values(data=event["data"])
            )
