"""Receipt-bound visual work after Core finishes; never changes execution or run results."""

import asyncio

from preact.core.models import Action, Observation, Prediction, identity, uid
from preact.visual.adapter import comparison, manifest
from preact.visual.renderer import RenderFailure


class VisualPostprocessor:
    """Serial, resumable postprocessing of saved events; no World/execution access.

    Reuse one processor for concurrent callers. Cross-process writers must be externally
    serialized; duplicate/conflicting output records are rejected, never silently accepted.
    """

    def __init__(self, store, artifacts, adapter):
        self.store, self.artifacts, self.adapter = store, artifacts, adapter
        self._lock = asyncio.Lock()

    async def run(self, run_id):
        async with self._lock:
            events = self.store.read_events(run_id)
            run = self.store.get_run(run_id)
            finished = [e for e in events if e["kind"] == "finished"]
            if not run or len(finished) != 1 or finished[0]["data"] != run["result"]:
                raise ValueError("Postprocessing requires a saved, normally finished Core run")
            if run["status"] != run["result"].get("status") or self.store.pending_execution(run_id):
                raise ValueError("Core result or execution is incomplete")
            nodes = {
                e["data"]["node"]["id"]: e["data"]["node"] for e in events if e["kind"] == "node"
            }
            visuals = {e["data"]["node_id"]: e for e in events if e["kind"] == "visual_prediction"}
            outcomes = [e for e in events if e["kind"] == "outcome"]
            report = {"run_id": run_id, "completed": 0, "failed": 0, "reused": 0}
            for outcome in outcomes:
                status = await self._outcome(run_id, outcome, nodes, visuals, events)
                report[status] += 1
            return report

    def _sources(self, run_id, outcome, nodes, visuals, events):
        node_id = outcome["data"]["node_id"]
        obs = Observation.model_validate(outcome["data"]["observation"])
        node = nodes[node_id]
        action = Action.model_validate(node["action"])
        execution = self.store.execution_record(obs.receipt)
        if (
            execution["status"] != "complete"
            or execution["run_id"] != run_id
            or execution["state_id"] != node["state"]["id"]
            or execution["action_hash"] != action.fingerprint
            or execution["receipt"] != obs.model_dump()
        ):
            raise ValueError("Outcome does not match its complete execution receipt")
        event = visuals[node_id]
        visual = Prediction.model_validate(manifest(self.artifacts, event["data"]["artifact"]))
        if (
            visual.model_dump() != event["data"]["prediction"]
            or visual.evidence != "generated_visual"
            or visual.mandatory_checks
            or visual.claim_results
            or visual.success.value is not None
            or visual.risk.value is not None
            or visual.state_id != action.state_id
            or visual.action_ids != [action.id]
            or event["seq"] >= outcome["seq"]
        ):
            raise ValueError("Visual prediction is not the saved pre-execution candidate")
        predicted = manifest(self.artifacts, visual.artifacts["manifest"])
        provenance = predicted["provenance"]
        source_event = next(e for e in events if e["seq"] == event["data"]["source_event_seq"])
        if (
            source_event["kind"] != "prediction"
            or source_event["data"]["node_id"] != node_id
            or source_event["seq"] >= event["seq"]
            or manifest(self.artifacts, provenance["state"]) != node["state"]
            or manifest(self.artifacts, provenance["action"]) != action.model_dump()
            or provenance["action_fingerprint"] != action.fingerprint
            or manifest(self.artifacts, provenance["mujoco_prediction"])
            != source_event["data"]["prediction"]
            or manifest(self.artifacts, source_event["data"]["artifact"])
            != source_event["data"]["prediction"]
        ):
            raise ValueError("Prediction source/state/action binding changed")
        if predicted["request"]["settings"] != self.adapter.settings.model_dump(mode="json"):
            raise ValueError("Postprocessing must retain prediction camera/render settings")
        for frame in predicted["frames"]:
            for digest in frame["artifacts"].values():
                self.artifacts.read(digest)
        return obs, visual

    async def _outcome(self, run_id, outcome, nodes, visuals, events):
        node_id = outcome["data"]["node_id"]
        receipt = outcome["data"]["observation"]["receipt"]
        visual_artifact = visuals.get(node_id, {}).get("data", {}).get("artifact")
        binding = {
            "run_id": run_id,
            "node_id": node_id,
            "receipt": receipt,
            "outcome_seq": outcome["seq"],
            "visual_prediction_artifact": visual_artifact,
        }
        binding["job_key"] = identity(binding)
        binding["attempt_id"] = uid()
        stage, actual = "validate_sources", None
        digest = None
        missing = [
            "observed_rgb",
            "observed_depth",
            "observed_segmentation",
            "observed_manifest",
            "comparison",
        ]
        try:
            obs, visual = self._sources(run_id, outcome, nodes, visuals, events)
            saved = self.store.read_events(run_id)
            rendered = [
                e
                for e in saved
                if e["kind"] == "visual_observed_result" and e["data"]["node_id"] == node_id
            ]
            compared = [
                e
                for e in saved
                if e["kind"] == "visual_comparison" and e["data"]["node_id"] == node_id
            ]
            if len(rendered) > 1 or len(compared) > 1 or (compared and not rendered):
                raise ValueError("Duplicate or incomplete postprocessing records")
            for saved_event in [*rendered, *compared]:
                if "job_key" in saved_event["data"] and any(
                    saved_event["data"].get(k) != v for k, v in binding.items() if k != "attempt_id"
                ):
                    raise ValueError("Saved visual record belongs to another job/receipt")
            if rendered:
                stage = "load_observed"
                actual = rendered[0]["data"]["manifest"]
                if rendered[0]["data"]["receipt"] != receipt:
                    raise ValueError("Saved observed rendering belongs to another receipt")
                m = manifest(self.artifacts, actual)
                for frame in m["frames"]:
                    for digest in frame["artifacts"].values():
                        self.artifacts.read(digest)
            else:
                stage = "render_observed"
                actual = await self.adapter.current(obs.state, receipt=receipt)
                # Bind and validate before publishing a successful observed rendering.
                self._observed(actual, obs)
                await self.store.call(
                    "append", run_id, "visual_observed_result", {**binding, "manifest": actual}
                )
            self._observed(actual, obs)
            missing = ["comparison"]
            stage = "compare"
            result = comparison(
                self.artifacts,
                self.store,
                run_id,
                visual,
                obs,
                actual,
                visual_artifact=visual_artifact,
            )
            if compared:
                if manifest(self.artifacts, compared[0]["data"]["artifact"]) != result:
                    raise ValueError("Saved comparison differs from its source artifacts")
                return "reused"
            stage = "publish_comparison"
            digest = self.artifacts.json(result)
            await self.store.call(
                "append", run_id, "visual_comparison", {**binding, "artifact": digest}
            )
            return "completed"
        except (Exception, asyncio.CancelledError) as error:
            # Core has already finished. Only append visual diagnostics; never set_status,
            # complete_execution, calibration, or any World method.
            retryable = isinstance(
                error, (RenderFailure, OSError, TimeoutError, asyncio.CancelledError)
            )
            # A durable append can commit before delivering cancellation to its caller.
            # Reconcile the acknowledgement rather than claiming its artifact is missing.
            committed = stage == "publish_comparison" and any(
                e["kind"] == "visual_comparison"
                and e["data"].get("job_key") == binding["job_key"]
                and e["data"].get("artifact") == digest
                for e in self.store.read_events(run_id)
            )
            if committed:
                missing = []
            failure = {
                **binding,
                "stage": stage,
                "exception_type": type(error).__name__,
                "message": str(error)[:4096],
                "cause_type": type(error.__cause__).__name__ if error.__cause__ else None,
                "observed_manifest": actual,
                "missing_artifacts": missing,
                "retryable": retryable,
                "comparison_committed": committed,
                "cleanup": getattr(getattr(self.adapter, "renderer", None), "last_cleanup", None)
                if stage == "render_observed"
                else None,
            }
            await self.store.call("append", run_id, "visual_postprocess_failed", failure)
            if isinstance(error, asyncio.CancelledError):
                raise
            return "failed"

    def _observed(self, digest, obs):
        m = manifest(self.artifacts, digest)
        if (
            m.get("synthetic") is not True
            or m.get("authority") != "none"
            or m["provenance"].get("receipt") != obs.receipt
            or manifest(self.artifacts, m["provenance"]["state"]) != obs.state.model_dump()
            or len(m["frames"]) != 1
            or m["frames"][0]["kind"] != "observed"
            or m["frames"][0]["phase"] != "observed_result"
            or m["frames"][0]["state_id"] != obs.state.id
        ):
            raise ValueError("Observed render does not match the complete receipt observation")
