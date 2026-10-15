"""Check the shipped, audited records and fail closed on archival corruption."""

import copy
import gzip
import json
from pathlib import Path

import pytest

from preact.core.models import identity
from preact.playground.recordings import RecordingCatalog, validate_recording
from preact.service.app import create_app

ROOT = Path(__file__).resolve().parents[1] / "assets/playground/recordings"


@pytest.fixture(scope="module")
def recordings():
    catalog = RecordingCatalog(ROOT)
    assert catalog.errors == [] and len(catalog.records) == 6
    return catalog.records


def test_reviewed_catalog_has_actual_experience_and_explicit_version_changes(recordings):
    for record in recordings.values():
        receipts, version, promotions = set(), None, 0
        for item in record.events:
            kind, data = item.event.kind, item.event.data
            if kind == "outcome":
                assert item.receipt_verified
                receipts.add(data["observation"]["receipt"])
                assert not data["observation"]["unsafe"]
            elif kind == "learning_checkpoint":
                current = data["backend"]["model_version"]
                assert version is None or version == current
                version = current
            elif kind == "learning_explicit_promotion":
                promoted = data["backend"]["epoch"]["model_version"]
                assert promoted != version
                version = promoted
                promotions += 1
        assert len(receipts) == record.session.rounds == 56
        assert promotions == record.session.provenance["promotions"]
        assert record.session.provenance["audit"]["task_receipts"] == 56


def test_adverse_low_to_high_result_is_retained(recordings):
    def reward(name):
        return sum(
            e.event.data["observation"]["metrics"]["reward"]
            for e in recordings[name].events
            if e.event.kind == "outcome"
        )

    assert reward("queue-low_to_high-learned-11") == pytest.approx(32.9)
    assert reward("queue-low_to_high-no_recovery-11") == pytest.approx(115.4)


@pytest.mark.parametrize("fault", ["checksum", "order", "receipt", "hypothesis", "choice"])
def test_recording_rejects_changed_or_incoherent_evidence(recordings, fault):
    value = copy.deepcopy(next(iter(recordings.values())).model_dump())
    if fault == "checksum":
        value["events"][0]["event"]["data"]["invented"] = True
    elif fault == "order":
        value["events"][0], value["events"][1] = value["events"][1], value["events"][0]
    elif fault == "receipt":
        item = next(e for e in value["events"] if e["event"]["kind"] == "outcome")
        item["receipt_verified"] = False
    elif fault == "hypothesis":
        item = next(e for e in value["events"] if e["event"]["kind"] == "observed")
        item["event"]["data"]["state"]["kind"] = "hypothetical"
    else:
        item = next(e for e in value["events"] if e["event"]["kind"] == "candidate_selection")
        item["event"]["data"]["action_fingerprint"] = "nonexistent"
    if fault != "checksum":
        value["digest"] = identity({k: v for k, v in value.items() if k != "digest"})
    with pytest.raises(ValueError):
        validate_recording(value)


def test_resealed_record_cannot_replace_a_pinned_export(recordings, tmp_path):
    value = copy.deepcopy(next(iter(recordings.values())).model_dump())
    old = value["digest"]
    value["session"]["label"] = "Unreviewed replacement"
    value["digest"] = identity({k: v for k, v in value.items() if k != "digest"})
    name = "case.json.gz"
    (tmp_path / name).write_bytes(gzip.compress(json.dumps(value).encode()))
    (tmp_path / "catalog.json").write_text(json.dumps({name: old}))
    catalog = RecordingCatalog(tmp_path)
    assert catalog.errors and not catalog.records


async def test_replay_gets_never_create_execution_or_learning_state(recordings, tmp_path):
    import httpx

    app = create_app(
        "sqlite:///" + str(tmp_path / "ledger.db"),
        str(tmp_path / "artifacts"),
        "local",
        playground_root=tmp_path / "sessions",
        playground_recordings=ROOT,
        kiosk=True,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for identifier, record in recordings.items():
                response = await client.get(f"/api/playground/sessions/{identifier}")
                assert response.status_code == 200
                assert len(response.json()["events"]) == len(record.events)
                refused = await client.post(
                    f"/api/playground/sessions/{identifier}/control",
                    json={"command": "step", "revision": 0},
                )
                assert refused.status_code == 409
            assert app.state.store.list_runs() == []
            assert app.state.store.error_rows() == []
