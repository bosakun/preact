"""Read existing events and receipts into a non-authoritative presentation stream."""

import json

from preact.cognition.memory import EpisodicMemory
from preact.core.models import RunEvent

from .models import PlaygroundEvent

KINDS = {
    "observed",
    "deliberation",
    "candidate_qualification",
    "candidate_selection",
    "gate_preview",
    "authorization",
    "decision",
    "execution_intent",
    "cognitive_multi_dispatch",
    "outcome",
    "cognitive_update",
    "learning_binding",
    "learning_checkpoint",
    "learning_round_open",
    "learning_experience",
    "learning_explicit_promotion",
    "continuous_epoch_started",
    "continuous_epoch_invalidated",
    "continuous_epoch_activated",
    "failed",
    "finished",
}

ARTIFACT_FIELDS = {
    "candidate": {
        "model_version",
        "dataset_hash",
        "effective_count",
        "source_cutoff",
        "training",
        "origin_artifact",
        "model_artifact",
    },
    "evaluation": {
        "passed",
        "reasons",
        "scores",
        "effective_count",
        "policy",
        "cutoff",
        "candidate_artifact",
        "code_hash",
    },
    "promotion": {
        "parent_version",
        "model_version",
        "candidate_artifact",
        "evaluation_artifact",
        "health_version",
        "cutoff",
        "code_hash",
    },
}


async def read_sources(store, sources):
    """Validate actual outcomes before any UI projection can call them experience."""
    rows = []
    memory = EpisodicMemory(store)
    for run_id, scope in sources.items():
        events = await store.call("read_events", run_id)
        confirmed = {e.reference for e in await memory.read(run_id)} if scope == "world" else set()
        for event in events:
            if event["kind"] not in KINDS:
                continue
            reference = f"{run_id}:{event['seq']}"
            verified = event["kind"] == "outcome" and reference in confirmed
            if event["kind"] == "outcome" and not verified:
                raise ValueError("Unconfirmed outcome cannot enter the playground")
            rows.append((event, reference, scope, verified))
    # This is a presentation order. Original timestamps and per-run sequence survive.
    rows.sort(key=lambda row: (row[0]["timestamp"], row[0]["run_id"], row[0]["seq"]))
    return [
        PlaygroundEvent(
            index=i,
            reference=ref,
            scope=scope,
            event=RunEvent.model_validate(event),
            receipt_verified=verified,
        )
        for i, (event, ref, scope, verified) in enumerate(rows, 1)
    ]


def artifact_details(artifacts, events):
    """Only allowlisted lifecycle summaries; never publish models or private files."""
    result = {}
    for item in events:
        data = item.event.data
        backend = data.get("backend", data.get("provenance", {}))
        if not isinstance(backend, dict):
            continue
        if item.event.kind == "learning_explicit_promotion":
            backend = {**backend, "promotion": backend.get("epoch", {}).get("promotion_artifact")}
        for field, allowed in ARTIFACT_FIELDS.items():
            digest = backend.get(field)
            if not digest or digest in result:
                continue
            record = json.loads(artifacts.read(digest))
            result[digest] = {"kind": field, **{k: v for k, v in record.items() if k in allowed}}
    return result
