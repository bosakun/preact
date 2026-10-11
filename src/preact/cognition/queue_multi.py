"""Pure value selection inside a Runtime-owned, fully resolved qualified set."""

from dataclasses import asdict

from preact.core.candidates import CandidateBatch
from preact.core.models import Action, identity

from .queue_model import QueueModelPlanner


class QueueMultiPlanner(QueueModelPlanner):
    multi_candidate = True

    def __init__(self, *args, selection_rule="model_value", **kwargs):
        if selection_rule not in {"model_value", "core_utility_control"}:
            raise ValueError("Unknown explicit selection rule")
        super().__init__(*args, **kwargs)
        self.selection_rule = selection_rule

    def select(self, batch: CandidateBatch) -> str:
        record = self.pending
        if record is None or identity(record) != self._seal or batch.run_id != self.run_id:
            raise ValueError("Missing or changed prediction record")
        if record["selection_rule"] != self.selection_rule or record["mode"] != self.mode:
            raise ValueError("Selection configuration changed")
        if batch.state.model_dump() != record["state"] or not batch.complete:
            raise ValueError("Qualification is not bound to the predicted State")
        predicted = {c["fingerprint"] for c in record["candidates"]}
        if predicted != {c.action.fingerprint for c in batch.candidates}:
            raise ValueError("Qualification proposal set differs from prediction")
        order = record["ranked_fingerprints"]
        qualified = [c for c in batch.candidates if c.status == "qualified"]
        if not qualified or any(c.action.fingerprint not in order for c in qualified):
            raise ValueError("No qualified value ranking")
        if record["selection_rule"] == "core_utility_control":
            return max(qualified, key=lambda c: c.evaluation.utility).node_id
        return min(qualified, key=lambda c: order.index(c.action.fingerprint)).node_id

    async def check(self, state, action=None):
        await super().check(state, action)
        if self.pending is None:
            return
        health = self.pending.get("health")
        if health is not None:
            guard = self.source.guard
            if (
                guard._model.version != health["model_version"]
                or asdict(guard._config) != health["config"]
                or list(guard._committed) != self.pending["monitor_runs"]
                or (health["status"] == "available" and guard._latched)
            ):
                raise ValueError("Model Health or monitoring scope changed")
        if self.pending["basis"] == "learned_model" and self.source.promotion is not None:
            active = self.source.recovery._active
            if active is None or active[0] != self.source.promotion:
                raise ValueError("Promoted model ceased to be active")

    def check_final_selection(self, events, action: Action):
        batches = [e["data"] for e in events if e["kind"] == "candidate_qualification"]
        selections = [e["data"] for e in events if e["kind"] == "candidate_selection"]
        if len(batches) != 1 or len(selections) != 1:
            raise ValueError("Missing or duplicate Runtime selection binding")
        batch = CandidateBatch.model_validate(batches[0]["batch"])
        chosen = self.select(batch)
        entry = next(c for c in batch.candidates if c.node_id == chosen)
        selection = selections[0]
        if (
            selection.get("batch_hash") != identity(batches[0]["batch"])
            or batches[0]["batch_hash"] != identity(batches[0]["batch"])
            or selection.get("node_id") != chosen
            or entry.action != action
            or selection.get("state_id") != batch.state.id
            or selection.get("action_fingerprint") != action.fingerprint
            or selection.get("evidence_hash") != entry.gate.evidence_hash
            or selection.get("policy_hash") != entry.gate.policy_hash
        ):
            raise ValueError("Runtime final selection binding changed")
