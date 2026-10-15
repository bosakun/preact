"""Read-only, checksum-verified exhibition projections. Never import execution truth."""

import gzip
import json
from pathlib import Path

from preact.core.models import Action, Observation, State, identity

from .models import PlaygroundRecording


def validate_recording(value):
    record = PlaygroundRecording.model_validate(value)
    content = record.model_dump(exclude={"digest"})
    if record.digest != identity(content) or record.session.mode != "recorded":
        raise ValueError("Replay checksum or mode differs")
    if record.session.provenance.get("audit_status") != "passed":
        raise ValueError("Replay needs an independently audited source")
    seen, sequences, observations, traces, selected, intents = set(), {}, {}, {}, {}, {}
    for index, item in enumerate(record.events, 1):
        e = item.event
        ref = f"{e.run_id}:{e.seq}"
        if item.index != index or item.reference != ref or ref in seen:
            raise ValueError("Replay event identity/order differs")
        if e.seq <= sequences.get(e.run_id, 0):
            raise ValueError("Replay reordered a source run")
        sequences[e.run_id] = e.seq
        seen.add(ref)
        data = e.data
        if e.kind == "observed":
            state = State.model_validate(data["state"])
            if state.kind != "observed":
                raise ValueError("Replay perception must be observed")
            observations[e.run_id] = state
        elif e.kind == "deliberation":
            if data["state"] != observations[e.run_id].model_dump():
                raise ValueError("Replay deliberation changed perception")
            traces[e.run_id] = data
        elif e.kind == "candidate_selection":
            action = data["action_fingerprint"]
            if action not in [
                f["fingerprint"] for f in traces[e.run_id]["simulation"]["candidates"]
            ]:
                raise ValueError("Replay selected a nonexistent candidate")
            selected[e.run_id] = action
        elif e.kind == "execution_intent":
            intents[e.run_id] = data["receipt"]
        elif e.kind == "outcome":
            actual = Observation.model_validate(data["observation"])
            if not item.receipt_verified or actual.receipt != intents.get(e.run_id):
                raise ValueError("Replay outcome lacks verified intent/receipt")
            choice = next(
                f
                for f in traces[e.run_id]["simulation"]["candidates"]
                if f["fingerprint"] == selected[e.run_id]
            )
            action = Action.model_validate(choice["action"])
            if (
                action.fingerprint != selected[e.run_id]
                or action.state_id != observations[e.run_id].id
            ):
                raise ValueError("Replay action binding differs")
    if record.session.event_count != len(record.events):
        raise ValueError("Replay event count differs")
    return record


class RecordingCatalog:
    def __init__(self, root):
        self.root = Path(root)
        self.records = {}
        self.errors = []
        if not self.root.exists():
            return
        manifest_path = self.root / "catalog.json"
        if not manifest_path.exists():
            self.errors.append("Missing replay catalog manifest")
            return
        try:
            manifest = json.loads(manifest_path.read_text())
        except (ValueError, OSError):
            self.errors.append("Invalid replay catalog manifest")
            return
        for path in sorted(self.root.glob("*.json.gz")):
            try:
                record = validate_recording(json.loads(gzip.decompress(path.read_bytes())))
                if manifest.get(path.name) != record.digest:
                    raise ValueError("Replay differs from the pinned catalog checksum")
                if record.session.id in self.records:
                    raise ValueError("Duplicate replay identity")
                self.records[record.session.id] = record
            except (ValueError, KeyError, TypeError, StopIteration, OSError) as error:
                self.errors.append(f"{path.name}: {type(error).__name__}")
        if set(manifest) != {p.name for p in self.root.glob("*.json.gz")}:
            self.records.clear()
            self.errors.append("Replay catalog files are missing or unlisted")

    def get(self, identifier):
        return self.records.get(identifier)
