import asyncio
import copy
import json
import subprocess
import sys
from collections import Counter

import pytest
from sqlalchemy import delete, event, update
from sqlalchemy.dialects import postgresql

from preact.cognition.memory import EpisodicMemory
from preact.core.models import Action, Observation, State
from preact.core.store import Store
from preact.domains.cognitive_queue import CognitiveQueueWorld
from tests.fixtures.memory_ledger import append_execution


class CountingStore(Store):
    def __init__(self, url="sqlite:///:memory:"):
        super().__init__(url)
        self.calls = Counter()

    async def call(self, method, *args, **kwargs):
        self.calls[method] += 1
        return await super().call(method, *args, **kwargs)


async def populated(store, count=3):
    memory = EpisodicMemory(store)
    world = CognitiveQueueWorld(ticks=30, target=100, seed=42)
    outcomes = {}
    for index in range(count):
        run_id = store.create_run({}, run_id=f"run-{index}")
        outcomes[run_id] = await append_execution(store, world, run_id)
        await memory.remember(run_id)
    store.calls.clear()
    return memory, world, outcomes


async def test_warm_retrieval_is_equal_without_full_event_or_receipt_reads():
    store = CountingStore()
    memory, world, _ = await populated(store)
    first = await memory.retrieve(world.task.domain)
    store.calls.clear()
    second = await memory.retrieve(world.task.domain)
    assert second == first and len(second) == 3
    assert store.calls == {"run_heads": 1}
    assert memory.cached_experiences == 3
    assert all(a is not b for a, b in zip(first, second))


async def test_changed_run_only_is_reread_and_new_execution_is_indexed():
    store = CountingStore()
    memory, world, _ = await populated(store)
    before = await memory.retrieve(world.task.domain)
    await append_execution(store, world, "run-2")
    store.calls.clear()
    after = await memory.retrieve(world.task.domain)
    assert store.calls == {"run_heads": 2, "read_events": 1, "execution_record": 2}
    assert len(after) == 4 and after[:3] == before
    store.calls.clear()
    assert await memory.retrieve(world.task.domain) == after
    assert store.calls == {"run_heads": 1}


async def test_returned_nested_objects_cannot_mutate_cache():
    store = CountingStore()
    memory, world, _ = await populated(store)
    original = await memory.retrieve(world.task.domain)
    returned = await memory.retrieve(world.task.domain)
    returned[0].observation.metrics["processed"] = 999
    returned[0].observation.state.payload["pending"].append({"due": 99, "amount": 99})
    returned[0].action.payload["amount"] = 999
    returned[0].input_state.payload["queue"] = 999
    returned[0].prediction_ids.clear()
    returned[0].reference = "forged-reference"
    returned.clear()
    assert await memory.retrieve(world.task.domain) == original


async def test_rebuild_and_domain_limit_order_match_full_reread():
    store = CountingStore()
    memory, world, _ = await populated(store)
    await append_execution(store, world, "run-1")
    memory.run_ids.append("run-2")  # Restored manifests may contain duplicate run IDs.
    all_records = []
    for run_id in reversed(list(dict.fromkeys(memory.run_ids))):
        all_records.extend(reversed(await memory.read(run_id)))
    for limit in (1, 2, 3, 20):
        expected = list(reversed(all_records[:limit]))
        assert await memory.retrieve(world.task.domain, limit) == expected
    assert await memory.retrieve("another-domain") == []
    assert await memory.retrieve(world.task.domain, 0) == []
    await memory.clear()
    assert memory.cached_experiences == 0
    assert await memory.retrieve(world.task.domain, 20) == list(reversed(all_records))
    assert memory.cached_experiences == 4


async def test_forged_duplicate_is_validated_before_dedup_and_repeated_failure_is_closed():
    store = CountingStore()
    memory, world, outcomes = await populated(store)
    before = await memory.retrieve(world.task.domain)
    store.append("run-2", "outcome", outcomes["run-2"])
    assert await memory.retrieve(world.task.domain) == before
    forged = copy.deepcopy(outcomes["run-2"])
    forged["observation"]["metrics"]["processed"] = 999
    store.append("run-2", "outcome", forged)
    for _ in range(2):
        with pytest.raises(ValueError, match="committed"):
            await memory.retrieve(world.task.domain)
    assert "run-2" not in memory._runs


async def test_unexecuted_branches_and_prediction_only_run_are_not_experiences():
    store = CountingStore()
    memory, world, _ = await populated(store)
    records = await memory.retrieve(world.task.domain)
    assert all("unexecuted" not in p for e in records for p in e.prediction_ids)
    assert len(records) == 3
    run_id = store.create_run({}, run_id="prediction-only")
    store.append(run_id, "prediction", {"node_id": "unexecuted", "prediction": {"id": "future"}})
    await memory.remember(run_id)
    assert run_id not in memory.run_ids
    memory.run_ids.append(run_id)
    assert await memory.retrieve(world.task.domain) == records


@pytest.mark.parametrize("change", ["status", "receipt", "state_id", "action_hash", "delete"])
async def test_receipt_only_change_invalidates_even_when_event_head_is_unchanged(change):
    store = CountingStore()
    memory, world, outcomes = await populated(store)
    await memory.retrieve(world.task.domain)
    receipt = outcomes["run-2"]["observation"]["receipt"]
    original = store.run_heads(["run-2"])["run-2"]
    with store.db.begin() as conn:
        where = store.executions.c.id == receipt
        if change == "delete":
            conn.execute(delete(store.executions).where(where))
        else:
            value = "pending" if change == "status" else "tampered"
            if change == "receipt":
                value = {**outcomes["run-2"]["observation"], "unsafe": True}
            conn.execute(update(store.executions).where(where).values(**{change: value}))
    current = store.run_heads(["run-2"])["run-2"]
    assert original.next_seq == current.next_seq
    assert original.execution_digest != current.execution_digest
    with pytest.raises(ValueError, match="committed"):
        await memory.retrieve(world.task.domain)


def test_read_only_heads_detect_pending_complete_abort_and_status_without_events():
    store = Store("sqlite:///:memory:")
    run_id = store.create_run({})
    state = State.create("test", {}, "actual-test")
    action = Action(name="test", kind="test", state_id=state.id, payload={})
    original = store.run_heads([run_id])[run_id]
    receipt = store.intent(run_id, state.id, action.fingerprint)
    pending = store.run_heads([run_id])[run_id]
    observation = Observation(state=state, success=False, unsafe=False, receipt=receipt)
    store.complete_execution(receipt, observation.model_dump())
    complete = store.run_heads([run_id])[run_id]
    second = store.intent(run_id, state.id, "other-action")
    store.abort_execution(second, "not dispatched")
    aborted = store.run_heads([run_id])[run_id]
    assert len({head.execution_digest for head in (original, pending, complete, aborted)}) == 4
    assert all(head.next_seq == 0 for head in (original, pending, complete, aborted))
    store.set_status(run_id, "complete")
    assert store.run_heads([run_id])[run_id].status == "complete"
    assert store.read_events(run_id) == []
    assert store.run_heads([]) == {} and store.run_heads(["missing"]) == {}


async def test_same_database_other_store_and_separate_process_updates_are_detected(tmp_path):
    url = f"sqlite:///{tmp_path / 'ledger.db'}"
    store = CountingStore(url)
    memory, world, outcomes = await populated(store)
    expected = await memory.retrieve(world.task.domain)
    writer = Store(url)
    writer.append("run-2", "diagnostic", {"external": True})
    store.calls.clear()
    assert await memory.retrieve(world.task.domain) == expected
    assert store.calls["read_events"] == 1
    forged = copy.deepcopy(outcomes["run-2"])
    forged["observation"]["unsafe"] = True
    script = (
        "import json, sys; from preact.core.store import Store; "
        "Store(sys.argv[1]).append('run-2', 'outcome', json.load(sys.stdin))"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, url],
        input=json.dumps(forged),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    with pytest.raises(ValueError, match="committed"):
        await memory.retrieve(world.task.domain)


async def test_failure_or_missing_run_never_returns_stale_cache(monkeypatch):
    store = CountingStore()
    memory, world, _ = await populated(store)
    await memory.retrieve(world.task.domain)
    original = store.run_heads

    def unavailable(_):
        raise OSError("authority unavailable")

    monkeypatch.setattr(store, "run_heads", unavailable)
    with pytest.raises(OSError, match="unavailable"):
        await memory.retrieve(world.task.domain)
    assert memory.cached_experiences == 0
    monkeypatch.setattr(store, "run_heads", original)
    assert len(await memory.retrieve(world.task.domain)) == 3
    with store.db.begin() as conn:
        conn.execute(delete(store.runs).where(store.runs.c.id == "run-2"))
    with pytest.raises(ValueError, match="authority"):
        await memory.retrieve(world.task.domain)


async def test_update_during_validation_does_not_publish_stale_index(monkeypatch):
    store = CountingStore()
    memory, world, _ = await populated(store)
    store.append("run-2", "diagnostic", {})
    original = store.execution_record
    changed = False

    def concurrent(receipt):
        nonlocal changed
        record = original(receipt)
        if not changed:
            changed = True
            store.append(record["run_id"], "diagnostic", {"during_read": True})
        return record

    monkeypatch.setattr(store, "execution_record", concurrent)
    with pytest.raises(ValueError, match="changed during"):
        await memory.retrieve(world.task.domain)
    assert "run-2" not in memory._runs
    assert len(await memory.retrieve(world.task.domain)) == 3


async def test_concurrent_retrieval_serializes_cache_publication():
    store = CountingStore()
    memory, world, _ = await populated(store)
    await memory.clear()
    first, second = await asyncio.gather(
        memory.retrieve(world.task.domain), memory.retrieve(world.task.domain)
    )
    assert first == second and memory.cached_experiences == 3
    assert store.calls["read_events"] == 3


def test_head_query_is_bounded_read_only_and_compiles_for_postgresql():
    store = Store("sqlite:///:memory:")
    ids = [store.create_run({}, run_id=f"run-{i}") for i in range(257)]
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, context.compiled.statement))

    event.listen(store.db, "before_cursor_execute", capture)
    heads = store.run_heads([*ids, ids[0], "missing"])
    assert len(heads) == 257 and len(statements) == 2
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in statements)
    assert all(
        "UNION ALL" in str(query.compile(dialect=postgresql.dialect())) for _, query in statements
    )


async def test_pending_receipt_is_not_an_experience_until_complete_and_outcome_append():
    store = CountingStore()
    world = CognitiveQueueWorld(ticks=3)
    run_id = store.create_run({})
    memory = EpisodicMemory(store)
    memory.run_ids = [run_id]
    assert await memory.retrieve(world.task.domain) == []
    state = await world.observe()
    action = world.action(state, 1)
    receipt = store.intent(run_id, state.id, action.fingerprint)
    node = {"id": "selected", "state": state.model_dump(), "action": action.model_dump()}
    store.append(run_id, "node", {"node": node})
    store.append(run_id, "execution_intent", {"node_id": "selected", "receipt": receipt})
    observation = await world.execute(action, receipt)
    store.append(
        run_id, "outcome", {"node_id": "selected", "observation": observation.model_dump()}
    )
    with pytest.raises(ValueError, match="committed"):
        await memory.retrieve(world.task.domain)
    head = store.run_heads([run_id])[run_id]
    store.complete_execution(receipt, observation.model_dump())
    assert store.run_heads([run_id])[run_id].next_seq == head.next_seq
    assert len(await memory.retrieve(world.task.domain)) == 1


async def test_external_process_receipt_only_update_is_detected(tmp_path):
    url = f"sqlite:///{tmp_path / 'ledger.db'}"
    store = CountingStore(url)
    memory, world, outcomes = await populated(store)
    await memory.retrieve(world.task.domain)
    receipt = outcomes["run-2"]["observation"]["receipt"]
    before = store.run_heads(["run-2"])["run-2"]
    script = (
        "import sys; from sqlalchemy import update; from preact.core.store import Store; "
        "store = Store(sys.argv[1]); "
        "conn = store.db.connect(); "
        "conn.execute(update(store.executions).where(store.executions.c.id == sys.argv[2])"
        ".values(status='pending')); conn.commit(); conn.close()"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, url, receipt],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    after = store.run_heads(["run-2"])["run-2"]
    assert after.next_seq == before.next_seq and after.execution_digest != before.execution_digest
    with pytest.raises(ValueError, match="committed"):
        await memory.retrieve(world.task.domain)


async def test_cancellation_evicts_consulted_cache_and_can_rebuild(monkeypatch):
    store = CountingStore()
    memory, world, _ = await populated(store)
    original = store.call
    started = asyncio.Event()
    released = asyncio.Event()

    async def blocked(method, *args, **kwargs):
        if method == "run_heads":
            started.set()
            await released.wait()
        return await original(method, *args, **kwargs)

    monkeypatch.setattr(store, "call", blocked)
    task = asyncio.create_task(memory.retrieve(world.task.domain))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert memory.cached_experiences == 0
    monkeypatch.setattr(store, "call", original)
    assert len(await memory.retrieve(world.task.domain)) == 3


async def test_real_cognitive_loop_matches_full_reread_actions_and_observations(
    tmp_path, monkeypatch
):
    from scripts.bench_cognitive_memory import FullRereadMemory
    from tests.test_cognition import agent_for

    current = CognitiveQueueWorld(ticks=18, target=20, seed=12, shift_tick=8)
    indexed = agent_for(current, tmp_path / "indexed")
    actual = await indexed.run(current, 18)
    monkeypatch.setattr("preact.cognition.loop.EpisodicMemory", FullRereadMemory)
    original = CognitiveQueueWorld(ticks=18, target=20, seed=12, shift_tick=8)
    baseline = agent_for(original, tmp_path / "baseline")
    before = await baseline.run(original, 18)
    assert [a.fingerprint for a in current.executed] == [a.fingerprint for a in original.executed]
    assert (actual.success, actual.unsafe, actual.status) == (
        before.success,
        before.unsafe,
        before.status,
    )
    assert actual.final_state.payload == before.final_state.payload
    assert current.reward == original.reward
    assert indexed.belief.inferred["service"].value == baseline.belief.inferred["service"].value
    after_records = await indexed.memory.retrieve(current.task.domain)
    before_records = await baseline.memory.retrieve(original.task.domain)
    assert [e.observation.metrics for e in after_records] == [
        e.observation.metrics for e in before_records
    ]


async def test_changed_node_or_forged_unexecuted_branch_requires_fresh_binding_validation():
    store = CountingStore()
    memory, world, outcomes = await populated(store)
    await memory.retrieve(world.task.domain)
    events = store.read_events("run-2")
    node = next(
        e["data"]["node"]
        for e in events
        if e["kind"] == "node" and e["data"]["node"]["id"] == outcomes["run-2"]["node_id"]
    )
    tampered = copy.deepcopy(node)
    tampered["action"]["payload"]["amount"] = 3
    store.append("run-2", "node_updated", {"node": tampered})
    with pytest.raises(ValueError, match="committed"):
        await memory.retrieve(world.task.domain)
    store.append("run-2", "node_updated", {"node": node})
    assert len(await memory.retrieve(world.task.domain)) == 3
    unexecuted = next(
        e["data"]["node"]
        for e in events
        if e["kind"] == "node" and "unexecuted" in e["data"]["node"]["id"]
    )
    store.append("run-2", "outcome", {**outcomes["run-2"], "node_id": unexecuted["id"]})
    with pytest.raises(ValueError, match="committed"):
        await memory.retrieve(world.task.domain)


async def test_appended_prediction_reference_and_run_status_are_reindexed():
    store = CountingStore()
    memory, world, outcomes = await populated(store)
    previous = await memory.retrieve(world.task.domain)
    store.append(
        "run-2",
        "prediction",
        {"node_id": outcomes["run-2"]["node_id"], "prediction": {"id": "late"}},
    )
    current = await memory.retrieve(world.task.domain)
    assert current[:-1] == previous[:-1]
    assert current[-1].prediction_ids == [*previous[-1].prediction_ids, "late"]
    store.calls.clear()
    store.set_status("run-2", "interrupted")
    assert await memory.retrieve(world.task.domain) == current
    assert store.calls == {"run_heads": 2, "read_events": 1, "execution_record": 1}
