import json

import pytest

from preact.cohorts import cases, episode, fixtures, freeze, validate_manifest
from preact.core.models import Action
from preact.core.store import Artifacts, Store
from preact.domains.program import ProgramWorld
from preact.replay import import_archive


def test_cohorts_distinct_and_protocol_refuses_changed_source(tmp_path):
    for domain, partitions in fixtures().items():
        assert [len(partitions[s]) for s in partitions] == [6, 12, 24]
        ids = [c["id"] for part in partitions.values() for c in part]
        hashes = [c["hash"] for part in partitions.values() for c in part]
        assert len(set(ids)) == len(set(hashes)) == 42
    path = tmp_path / "protocol.json"
    manifest = freeze(path, seeds=1)
    validate_manifest(manifest, None)
    with pytest.raises(FileExistsError):
        freeze(path)
    manifest["source_hash"] = "changed"
    with pytest.raises(ValueError, match="checksum/source"):
        validate_manifest(manifest, None)


@pytest.mark.parametrize("domain", ["software", "physical"])
async def test_development_executes_shared_core_and_only_labels_executed_predictions(
    tmp_path, domain
):
    spec = cases(domain, "development")[0]
    direct = await episode(spec, domain, 0, "direct", tmp_path)
    preact = await episode(spec, domain, 0, "preact", tmp_path)
    assert direct["result"]["status"] != "failed"
    assert preact["result"]["success"]
    assert not preact["result"]["unsafe"]
    for archive in (direct, preact):
        executed = {e["data"]["node_id"] for e in archive["events"] if e["kind"] == "outcome"}
        predictions = {
            e["data"]["prediction"]["id"]: e["data"]["node_id"]
            for e in archive["events"]
            if e["kind"] == "prediction"
        }
        assert archive["errors"]
        assert all(predictions[row["prediction_id"]] in executed for row in archive["errors"])
    assert any(e["kind"] == "selected_action_forecast" for e in direct["events"])
    assert not any(e["kind"] == "authorization" for e in direct["events"])
    path = tmp_path / "cohort.json"
    preact["protocol_hash"] = "recorded-protocol"
    path.write_text(json.dumps(preact))
    destination = Store("sqlite:///:memory:")
    restored = import_archive(str(path), destination, Artifacts(str(tmp_path / "restored")))
    assert destination.get_run(restored)["status"] == "recorded"
    assert not destination.error_rows()


async def test_all_development_program_reference_repairs_and_protected_labels(tmp_path):
    for spec in cases("software", "development"):
        world = ProgramWorld(spec, evaluation_seed=1729)
        state = await world.observe()
        assert "cases" not in json.dumps(state.payload)
        action = Action(
            name="reference",
            kind="patch",
            state_id=state.id,
            payload={"files": {"program.py": spec.source(spec.repair)}},
        )
        payload, checks, metrics, _ = await world.measure(state, action, 1729)
        assert payload["goal_complete"] and all(checks.values()), spec.name
        assert metrics["test_pass_fraction"] == 1


def test_model_protocol_freezes_equal_budgets_and_identity(tmp_path):
    profile = {
        "manifest": {"scope": "injected profile, not real inference"},
        "capabilities": {"version": "fixture"},
    }
    manifest = freeze(tmp_path / "model.json", seeds=1, model_profile=profile, max_seconds=300)
    assert all(p["max_seconds"] == 300 for p in manifest["policies"].values())
    assert manifest["model_profile"] == profile
    validate_manifest(manifest, None)
    manifest["model_profile"]["capabilities"]["version"] = "changed"
    with pytest.raises(ValueError, match="checksum/source"):
        validate_manifest(manifest, None)


async def test_model_episode_refuses_changed_profile_and_closes_client(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from preact.core.models import Capabilities
    from preact.engines.llamacpp import LocalNemotron

    closed = []

    async def close():
        closed.append(True)

    fake = SimpleNamespace(
        manifest=SimpleNamespace(public_identity={"scope": "injected mismatch fixture"}),
        capabilities=Capabilities(
            engine_id="fixture",
            version="fixture",
            family="fixture",
            domains=["software"],
            evidence="inference",
            tier=0,
            applicability="fixture only",
        ),
        aclose=close,
    )

    async def connect():
        return fake

    monkeypatch.setattr(LocalNemotron, "connect", connect)
    store = Store("sqlite:///:memory:")
    with pytest.raises(ValueError, match="frozen protocol"):
        await episode(
            cases("software", "development")[0],
            "software",
            0,
            "preact",
            tmp_path,
            store=store,
            model_profile={"scope": "other injected profile"},
        )
    assert closed and not store.list_runs() and not store.error_rows()
