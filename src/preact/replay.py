"""Import recorded benchmark evidence without running an agent or labeling new truth."""

import hashlib
from pathlib import Path

from pydantic import Field

from preact.core.models import Contract, Observation, Policy, Prediction, RunEvent, Task, TreeNode
from preact.core.store import Artifacts, Store


class Archive(Contract):
    task: Task
    policy: Policy
    events: list[RunEvent] = Field(min_length=1)
    result: dict
    # Cohort metadata is preserved in the source checksum, never imported as
    # execution truth or used to update the destination calibration ledger.
    errors: list[dict] = Field(default_factory=list)
    protocol_hash: str | None = None


def import_archive(path: str, store: Store, artifacts: Artifacts, artifact_source=None):
    source = Path(path)
    encoded = source.read_bytes()
    archive = Archive.model_validate_json(encoded)
    original = archive.events[0].run_id
    references = set()
    for index, event in enumerate(archive.events, 1):
        if event.seq != index or event.run_id != original:
            raise ValueError("Archive must contain one contiguous ordered run")
        if event.kind in {"node", "node_updated"}:
            TreeNode.model_validate(event.data["node"])
        elif event.kind == "prediction":
            prediction = Prediction.model_validate(event.data["prediction"])
            references.update(prediction.artifacts.values())
            references.add(event.data["artifact"])
        elif event.kind == "outcome":
            observation = Observation.model_validate(event.data["observation"])
            references.update(observation.artifacts.values())
    source_artifacts = Artifacts(str(artifact_source or source.parent / "artifacts"))
    # Validate every referenced artifact before publishing any run. Digest validation
    # prevents path escape and corruption; archival integrity is not proof of live use.
    content = {digest: source_artifacts.read(digest) for digest in references}
    for digest, data in content.items():
        assert artifacts.put(data) == digest
    run_id = store.create_run(
        {
            "request": {
                "domain": archive.task.domain,
                "mode": "recorded",
                "task_id": archive.task.id,
            },
            "task": archive.task.model_dump(),
            "policy": archive.policy.model_dump(),
            "environment": "recorded",
            "original_run_id": original,
            "archive_sha256": hashlib.sha256(encoded).hexdigest(),
            "scope": "Recorded evidence; never an execution or new calibration observation",
        }
    )
    for event in archive.events:
        store.append(run_id, event.kind, {**event.data, "recorded_timestamp": event.timestamp})
    store.set_status(run_id, "recorded", {**archive.result, "status": "recorded", "recorded": True})
    return run_id
