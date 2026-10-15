"""Generate the shared JSON Schema without importing service or vendor dependencies."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from preact.core.models import (
    Action,
    Capabilities,
    FutureOutcome,
    GateResult,
    Observation,
    Policy,
    Prediction,
    PredictionRequest,
    RunEvent,
    State,
    Task,
    TreeNode,
)

schema = TypeAdapter(
    State
    | Action
    | Capabilities
    | FutureOutcome
    | GateResult
    | Observation
    | Policy
    | Prediction
    | PredictionRequest
    | RunEvent
    | Task
    | TreeNode
).json_schema(mode="serialization")
# The API serializes defaults with model_dump; consumers see all declared fields.
for definition in schema.get("$defs", {}).values():
    if "properties" in definition:
        definition["required"] = list(definition["properties"])
Path("web/src/contracts.schema.json").write_text(json.dumps(schema, indent=2) + "\n")

# Exhibition contracts do not extend or change Core's execution models.
from preact.playground.models import (  # noqa: E402
    PlaygroundCommand,
    PlaygroundEvent,
    PlaygroundRecording,
    PlaygroundSession,
    PlaygroundSnapshot,
    PlaygroundStart,
)

playground = TypeAdapter(
    PlaygroundStart
    | PlaygroundCommand
    | PlaygroundEvent
    | PlaygroundSession
    | PlaygroundSnapshot
    | PlaygroundRecording
).json_schema(mode="serialization")
for definition in playground.get("$defs", {}).values():
    if "properties" in definition:
        definition["required"] = list(definition["properties"])
Path("web/src/playground.schema.json").write_text(json.dumps(playground, indent=2) + "\n")
