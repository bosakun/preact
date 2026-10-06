import base64
import sqlite3

import pytest

from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.repository import RepositoryWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


async def test_actual_repository_workflow_preserves_data_and_verifies_each_operation(tmp_path):
    world, store = RepositoryWorld(seed=3), Store("sqlite:///:memory:")
    initial = (await world.observe()).id
    runtime = Runtime(
        store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world), LocalVerifier(world)])
    )
    result = await runtime.run(world)
    assert result["success"] and not result["unsafe"] and result["steps"] == 4
    events = store.read_events(runtime.run_id)
    actions = [
        e["data"]["action"]["kind"]
        for e in events
        if e["kind"] == "decision" and e["data"]["decision"] == "execute"
    ]
    assert actions == ["patch", "migration", "configuration", "command"]
    rejected = [
        n["data"]["node"]
        for n in events
        if n["kind"] == "node_updated" and n["data"]["node"]["status"] == "rejected"
    ]
    assert any("data_preservation" in n["evaluation"]["violations"] for n in rejected)
    assert any("schema_integrity" in n["evaluation"]["violations"] for n in rejected)
    db = sqlite3.connect(":memory:")
    db.deserialize(base64.b64decode(result["final_state"]["payload"]["database_b64"]))
    assert db.execute("SELECT id,total FROM orders ORDER BY id").fetchall() == world.expected_rows
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO orders(id,total,discount) VALUES(999,1,-1)")
    db.close()
    assert initial != result["final_state"]["id"]


async def test_prediction_branch_leaves_authority_unchanged_and_shell_input_is_rejected():
    world = RepositoryWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    await world.verify_future(state, action, 0)
    assert (await world.observe()).id == state.id
    action.kind, action.payload = "command", {"command": "rm -rf /"}
    with pytest.raises(ValueError, match="allowlisted"):
        world.validate(state, action)
