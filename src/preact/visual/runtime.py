"""Optional observation-only Runtime instrumentation; Core authority stays unchanged."""

from preact.core.models import Observation, Prediction, State, TreeNode
from preact.core.runtime import Runtime
from preact.visual.adapter import comparison


class VisualRuntime(Runtime):
    def __init__(self, *args, visual_adapter, **kwargs):
        super().__init__(*args, **kwargs)
        self.visual_adapter = visual_adapter
        self.visual_nodes = {}
        self.visual_predictions = {}

    async def emit(self, kind, data):
        if kind == "decision" and data.get("decision") == "execute":
            if data["node_id"] not in self.visual_predictions:
                raise RuntimeError("Selected candidate has no completed visual representation")
        event = await super().emit(kind, data)
        adapter = self.visual_adapter
        if kind == "node":
            node = TreeNode.model_validate(data["node"])
            self.visual_nodes[node.id] = node
        elif kind == "observed":
            digest = await adapter.current(State.model_validate(data["state"]))
            await super().emit(
                "visual_current", {"manifest": digest, "state_id": data["state"]["id"]}
            )
        elif kind == "prediction" and data["prediction"]["engine_id"] == "mujoco":
            node = self.visual_nodes[data["node_id"]]
            visual = await adapter.prediction(
                node.state, node.action, Prediction.model_validate(data["prediction"])
            )
            self.visual_predictions[node.id] = visual
            await super().emit(
                "visual_prediction",
                {
                    "node_id": node.id,
                    "prediction": visual.model_dump(),
                    "artifact": self.artifacts.json(visual.model_dump()),
                    "source_event_seq": event.seq,
                },
            )
        elif kind == "outcome":
            observation = Observation.model_validate(data["observation"])
            digest = await adapter.current(observation.state, receipt=observation.receipt)
            await super().emit(
                "visual_observed_result",
                {"node_id": data["node_id"], "manifest": digest, "receipt": observation.receipt},
            )
            visual = self.visual_predictions.get(data["node_id"])
            if visual:
                result = comparison(
                    self.artifacts, self.store, self.run_id, visual, observation, digest
                )
                await super().emit(
                    "visual_comparison",
                    {"node_id": data["node_id"], "artifact": self.artifacts.json(result)},
                )
        return event
