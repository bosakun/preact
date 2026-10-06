import json

import pytest

from preact.core.models import identity
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier
from preact.replay import import_archive


async def archive_run(root):
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")
    runtime = Runtime(
        store,
        Artifacts(str(root / "artifacts")),
        Registry([LocalHeuristic(world), LocalVerifier(world)]),
    )
    result = await runtime.run(world)
    path = root / "episode.json"
    path.write_text(
        json.dumps(
            {
                "task": world.task.model_dump(),
                "policy": runtime.policy.model_dump(),
                "events": store.read_events(runtime.run_id),
                "result": result,
            }
        )
    )
    return path, runtime.run_id


async def test_archive_replay_preserves_evidence_without_executions_or_calibration(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path, original = await archive_run(source)
    destination = Store("sqlite:///:memory:")
    artifacts = Artifacts(str(tmp_path / "destination"))
    run_id = import_archive(str(path), destination, artifacts)
    assert run_id != original
    record = destination.get_run(run_id)
    assert record["status"] == "recorded" and record["result"]["recorded"]
    assert record["config"]["original_run_id"] == original
    assert not destination.error_rows()
    assert not destination.pending_execution(run_id)
    events = destination.read_events(run_id)
    for archived, restored in zip(json.loads(path.read_text())["events"], events):
        assert archived["kind"] == restored["kind"]
        assert restored["data"]["recorded_timestamp"] == archived["timestamp"]
        assert identity(archived["data"]) == identity(
            {k: v for k, v in restored["data"].items() if k != "recorded_timestamp"}
        )
        if restored["kind"] == "prediction":
            assert artifacts.read(restored["data"]["artifact"])


async def test_invalid_order_and_corrupt_artifacts_never_publish_recorded_run(tmp_path):
    path, _ = await archive_run(tmp_path)
    content = json.loads(path.read_text())
    destination = Store("sqlite:///:memory:")
    artifacts = Artifacts(str(tmp_path / "destination"))
    broken = tmp_path / "broken.json"
    content["events"][0]["seq"] = 99
    broken.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="contiguous"):
        import_archive(str(broken), destination, artifacts)
    assert destination.list_runs() == []
    content = json.loads(path.read_text())
    digest = next(e["data"]["artifact"] for e in content["events"] if e["kind"] == "prediction")
    (tmp_path / "artifacts" / digest).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="integrity"):
        import_archive(str(path), destination, artifacts)
    assert destination.list_runs() == []
