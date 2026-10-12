"""Independent read-only auditor: source reconstruction and analytic ray geometry.

Does not call the renderer, production translator, projection or comparison code.
"""

import io
import json
import math

import numpy as np

from preact.core.models import Action, Observation, Prediction, State, identity


def read_json(artifacts, digest):
    data = artifacts.read(digest)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Oversized audit input")
    return json.loads(data)


def expected_geometry(payload):
    half = payload.get("cube_half", 0.025)
    objects = [
        ("floor", 1, "box", [0, 0, -0.05], [2, 2, 0.05]),
        ("cube", 2, "box", payload["object"], [half] * 3),
        ("target", 3, "box", payload["target"], [0.04, 0.04, 0.002]),
        ("gripper", 4, "sphere", payload["object"], [0.018] * 3),
    ]
    boxes = [
        {"center": payload["obstacle"], "half": payload["obstacle_half"]},
        *payload.get("extra_obstacles", []),
    ]
    objects += [
        (f"obstacle_{i}", 5 + i, "box", box["center"], box["half"]) for i, box in enumerate(boxes)
    ]
    return [
        {"name": name, "object_id": oid, "geometry": geometry, "center": center, "half": half}
        for name, oid, geometry, center, half in objects
    ]


def camera_matrices(settings):
    c = settings["camera"]
    origin = np.array(c["position"], dtype=float)
    z = np.array(c["look_at"], dtype=float) - origin
    z /= np.linalg.norm(z)
    x = np.cross(z, np.array([0, 0, 1.0]))
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    rotation = np.array([x, y, z])
    ext = np.eye(4)
    ext[:3, :3] = rotation
    ext[:3, 3] = -rotation @ origin
    f = settings["width"] * c["focal_mm"] / c["sensor_mm"]
    k = np.array([[f, 0, settings["width"] / 2], [0, f, settings["height"] / 2], [0, 0, 1]])
    return k, ext


def analytic_image(objects, settings):
    k, ext = camera_matrices(settings)
    y, x = np.indices((settings["height"], settings["width"]))
    directions_cv = np.stack(
        [(x + 0.5 - k[0, 2]) / k[0, 0], (y + 0.5 - k[1, 2]) / k[1, 1], np.ones_like(x)], axis=-1
    )
    directions = directions_cv @ ext[:3, :3]
    origin = np.array(settings["camera"]["position"])
    depth = np.full(x.shape, np.inf)
    ids = np.zeros(x.shape, dtype=np.uint16)
    for obj in objects:
        center, half = np.array(obj["center"]), np.array(obj["half"])
        if obj["geometry"] == "box":
            with np.errstate(divide="ignore", invalid="ignore"):
                a, b = (center - half - origin) / directions, (center + half - origin) / directions
            near = np.max(np.minimum(a, b), axis=-1)
            far = np.min(np.maximum(a, b), axis=-1)
            distance = np.where((far >= near) & (near > 0), near, np.inf)
        else:
            offset = origin - center
            a = np.sum(directions * directions, axis=-1)
            b = 2 * (directions @ offset)
            c = offset @ offset - half[0] ** 2
            discriminant = b * b - 4 * a * c
            distance = np.where(
                discriminant >= 0, (-b - np.sqrt(np.maximum(0, discriminant))) / (2 * a), np.inf
            )
            distance = np.where(distance > 0, distance, np.inf)
        closer = distance < depth
        depth[closer] = distance[closer]
        ids[closer] = obj["object_id"]
    depth[~np.isfinite(depth)] = 0
    return depth, ids


def audit_manifest(artifacts, digest):
    m = read_json(artifacts, digest)
    if (
        m["authority"] != "none"
        or m["synthetic"] is not True
        or identity(m["request"]) != m["request_hash"]
    ):
        raise ValueError("Manifest semantics/hash mismatch")
    state = State.model_validate(read_json(artifacts, m["provenance"]["state"]))
    if state.kind != "observed" or state.domain != "physical":
        raise ValueError("Root must be a physical observation")
    source = None
    if "mujoco_prediction" in m["provenance"]:
        action = Action.model_validate(read_json(artifacts, m["provenance"]["action"]))
        source = Prediction.model_validate(
            read_json(artifacts, m["provenance"]["mujoco_prediction"])
        )
        if (
            action.state_id != state.id
            or action.fingerprint != m["provenance"]["action_fingerprint"]
            or source.state_id != state.id
            or source.action_ids != [action.id]
            or source.engine_id != "mujoco"
            or source.evidence != "simulation"
            or source.horizon != 1
            or source.successor is None
            or source.successor.kind != "hypothetical"
            or source.successor.parent_id != state.id
            or source.id != m["provenance"]["mujoco_prediction_id"]
            or source.raw["trajectory"] != source.successor.payload["trajectory"]
        ):
            raise ValueError("Source/action/state lineage mismatch")
    settings = m["request"]["settings"]
    k, ext = camera_matrices(settings)
    if len(m["frames"]) != len(m["request"]["frames"]):
        raise ValueError("Incomplete frames")
    results = []
    for frame, requested in zip(m["frames"], m["request"]["frames"], strict=True):
        for key in ("state_id", "kind", "phase", "trajectory_index"):
            if frame[key] != requested[key]:
                raise ValueError("Frame metadata mismatch")
        if frame["phase"] in {"current", "observed_result"}:
            expected_state = state
            if frame["kind"] != "observed" or (frame["phase"] == "observed_result") != bool(
                m["provenance"].get("receipt")
            ):
                raise ValueError("Observation mislabeled")
        else:
            if source is None or frame["kind"] != "hypothetical":
                raise ValueError("Unbacked hypothetical state")
            expected_state = source.successor
            if frame["phase"] == "trajectory":
                point = source.raw["trajectory"][frame["trajectory_index"]]
                expected_state = State.create(
                    "physical",
                    {**state.payload, "object": point},
                    "audited-trace",
                    kind="hypothetical",
                )
        if expected_state.id != frame["state_id"]:
            raise ValueError("Frame state mismatch")
        objects = expected_geometry(expected_state.payload)
        if len(objects) != len(frame["objects"]) or len(objects) != len(requested["objects"]):
            raise ValueError("Missing/extra geometry")
        for expected, actual, requested_obj in zip(
            objects, frame["objects"], requested["objects"], strict=True
        ):
            for key in ("name", "object_id", "geometry"):
                if expected[key] != actual[key] or expected[key] != requested_obj[key]:
                    raise ValueError("Geometry identity mismatch")
            for key in ("center", "half"):
                if not np.allclose(
                    expected[key], actual[key], atol=2e-7, rtol=0
                ) or not np.array_equal(expected[key], requested_obj[key]):
                    raise ValueError("Geometry coordinate/dimension mismatch")
        if not np.allclose(k, frame["intrinsic"], atol=1e-8, rtol=0) or not np.allclose(
            ext, frame["world_to_camera_cv"], atol=2e-6, rtol=0
        ):
            raise ValueError("Camera matrix mismatch")
        c2w = np.array(frame["camera_to_world_blender"])
        if not np.allclose(np.diag([1, -1, -1, 1]) @ np.linalg.inv(c2w), ext, atol=2e-6, rtol=0):
            raise ValueError("Camera convention mismatch")
        depth, ids = analytic_image(objects, settings)
        arrays = {}
        for name in ("rgb", "depth", "segmentation"):
            data = artifacts.read(frame["artifacts"][name])
            if name != "rgb":
                arrays[name] = np.load(io.BytesIO(data), allow_pickle=False)
        actual_depth, actual_ids = arrays["depth"], arrays["segmentation"]
        if (
            actual_depth.shape != depth.shape
            or actual_ids.shape != ids.shape
            or actual_depth.dtype != np.float32
            or actual_ids.dtype != np.uint16
        ):
            raise ValueError("Dense format mismatch")
        if not np.all(np.isfinite(actual_depth)) or not set(np.unique(actual_ids)) <= {
            0,
            *(o["object_id"] for o in objects),
        }:
            raise ValueError("Dense values/IDs invalid")
        mismatch = int(np.count_nonzero(ids != actual_ids))
        # Polygonal sphere has a small silhouette difference from analytic sphere.
        non_sphere = (ids != 4) & (actual_ids != 4)
        if np.any((ids != actual_ids) & non_sphere) or mismatch > max(4, ids.size * 0.001):
            raise ValueError("Segmentation disagrees with analytic geometry")
        error = np.abs(depth - actual_depth)
        if np.max(error[non_sphere], initial=0) > 2e-5:
            raise ValueError("Metric box depth disagrees with analytic geometry")
        sphere_match = (ids == 4) & (actual_ids == 4)
        if np.max(error[sphere_match], initial=0) > 0.002:
            raise ValueError("Sphere depth exceeds mesh approximation tolerance")
        bbox_error = 0.0
        for obj in objects:
            if obj["name"] == "floor":
                continue
            center, half = np.array(obj["center"]), np.array(obj["half"])
            corners = np.array(
                [center + half * [a, b, c] for a in [-1, 1] for b in [-1, 1] for c in [-1, 1]]
            )
            projected = (corners @ ext[:3, :3].T + ext[:3, 3]) @ k.T
            uv = projected[:, :2] / projected[:, 2, None]
            yy, xx = np.nonzero(actual_ids == obj["object_id"])
            if len(xx):
                excess = max(
                    float(uv[:, 0].min() - (xx + 0.5).min()),
                    float((xx + 0.5).max() - uv[:, 0].max()),
                    float(uv[:, 1].min() - (yy + 0.5).min()),
                    float((yy + 0.5).max() - uv[:, 1].max()),
                    0,
                )
                bbox_error = max(bbox_error, excess)
        if bbox_error > 0.001:
            raise ValueError("Segmented pixels exceed independently projected object bounds")
        results.append(
            {
                "phase": frame["phase"],
                "object_count": len(objects),
                "box_depth_max_error_m": float(np.max(error[non_sphere], initial=0)),
                "sphere_depth_max_error_m": float(np.max(error[sphere_match], initial=0)),
                "segmentation_mismatch_pixels": mismatch,
                "visible_ids": [int(i) for i in np.unique(actual_ids)],
                "projection_bbox_excess_px": bbox_error,
            }
        )
    return {"manifest": digest, "passed": True, "frames": results}


def audit_run(store, artifacts, run_id):
    events = store.read_events(run_id)
    if any("job_key" in e["data"] for e in events if e["kind"] == "visual_comparison"):
        audit_postprocessing(store, artifacts, run_id)
    nodes, sources, visuals, outcomes = {}, {}, {}, {}
    intents, decisions, authorizations = {}, {}, []
    current_observation = None
    checks = []
    for event in events:
        data, kind = event["data"], event["kind"]
        if kind == "observed":
            current_observation = data["state"]
        elif kind == "node":
            nodes[data["node"]["id"]] = data["node"]
        elif kind == "prediction":
            if read_json(artifacts, data["artifact"]) != data["prediction"]:
                raise ValueError("Saved prediction differs from event")
            sources[event["seq"]] = data
        elif kind == "visual_prediction":
            visual = Prediction.model_validate(data["prediction"])
            if (
                visual.evidence != "generated_visual"
                or visual.mandatory_checks
                or visual.claim_results
                or visual.success.value is not None
                or visual.risk.value is not None
            ):
                raise ValueError("Visual output acquired claims/authority")
            if read_json(artifacts, data["artifact"]) != visual.model_dump():
                raise ValueError("Visual artifact/event mismatch")
            source = sources[data["source_event_seq"]]
            m = read_json(artifacts, visual.artifacts["manifest"])
            if (
                source["node_id"] != data["node_id"]
                or read_json(artifacts, m["provenance"]["mujoco_prediction"])
                != source["prediction"]
            ):
                raise ValueError("Visual source not saved Runtime prediction")
            node = nodes[data["node_id"]]
            if (
                read_json(artifacts, m["provenance"]["state"]) != node["state"]
                or read_json(artifacts, m["provenance"]["action"]) != node["action"]
            ):
                raise ValueError("Source not Runtime candidate")
            checks.append(audit_manifest(artifacts, visual.artifacts["manifest"]))
            visuals[data["node_id"]] = (event["seq"], visual)
        elif kind in {"visual_current", "visual_observed_result"}:
            m = read_json(artifacts, data["manifest"])
            rendered_state = read_json(artifacts, m["provenance"]["state"])
            if kind == "visual_current" and rendered_state != current_observation:
                raise ValueError("Current visual is not the saved Runtime observation")
            if kind == "visual_observed_result":
                obs = outcomes[data["node_id"]]
                if (
                    rendered_state != obs.state.model_dump()
                    or m["provenance"]["receipt"] != obs.receipt
                ):
                    raise ValueError("Observed visual is not the saved outcome")
            checks.append(audit_manifest(artifacts, data["manifest"]))
        elif kind == "decision" and data.get("decision") == "execute":
            if data["node_id"] not in visuals or visuals[data["node_id"]][0] >= event["seq"]:
                raise ValueError("Selected prediction not rendered before dispatch")
            if data.get("direct"):
                raise ValueError("Visual benchmark must use the gated Runtime")
            decisions[data["node_id"]] = event["seq"]
        elif kind == "authorization":
            if data["decision"] != "execute":
                raise ValueError("Execution authorization not granted")
            authorizations.append((event["seq"], data))
        elif kind == "execution_intent":
            if data["node_id"] not in decisions or decisions[data["node_id"]] >= event["seq"]:
                raise ValueError("Intent lacks prior selected decision")
            intents[data["receipt"]] = (event["seq"], data["node_id"])
        elif kind == "outcome":
            obs = Observation.model_validate(data["observation"])
            node = nodes[data["node_id"]]
            action = Action.model_validate(node["action"])
            record = store.execution_record(obs.receipt)
            if obs.receipt not in intents or intents[obs.receipt][1] != data["node_id"]:
                raise ValueError("Outcome lacks matching prior durable intent")
            if not any(
                a["state_id"] == node["state"]["id"]
                and a["action_hash"] == action.fingerprint
                and seq < decisions[data["node_id"]]
                for seq, a in authorizations
            ):
                raise ValueError("Outcome lacks matching prior Gate authorization")
            if (
                record["status"] != "complete"
                or record["receipt"] != obs.model_dump()
                or record["run_id"] != run_id
                or record["state_id"] != node["state"]["id"]
                or record["action_hash"] != action.fingerprint
            ):
                raise ValueError("Invalid complete receipt")
            outcomes[data["node_id"]] = obs
        elif kind == "visual_comparison":
            c = read_json(artifacts, data["artifact"])
            obs = outcomes[data["node_id"]]
            if (
                c["receipt"] != obs.receipt
                or read_json(artifacts, c["observation"]) != obs.model_dump()
                or c["execution"] != store.execution_record(obs.receipt)
            ):
                raise ValueError("Unexecuted candidate given actual outcome")
            _, visual = visuals[data["node_id"]]
            if read_json(artifacts, c["visual_prediction"]) != visual.model_dump():
                raise ValueError("Comparison source changed")
            if c["predicted_manifest"] != visual.artifacts["manifest"]:
                raise ValueError("Comparison refers to another candidate manifest")
            predicted = read_json(artifacts, c["predicted_manifest"])
            source = read_json(artifacts, predicted["provenance"]["mujoco_prediction"])
            actual = read_json(artifacts, c["actual_manifest"])
            if (
                actual["provenance"]["receipt"] != obs.receipt
                or read_json(artifacts, actual["provenance"]["state"]) != obs.state.model_dump()
            ):
                raise ValueError("Actual render is not receipt observation")
            if actual["request"]["settings"] != predicted["request"]["settings"]:
                raise ValueError("Comparison camera changed")
            pp, ap = source["successor"]["payload"]["object"], obs.state.payload["object"]
            if (
                c["position_error_m"] != math.dist(pp, ap)
                or c["predicted_position_m"] != pp
                or c["observed_position_m"] != ap
            ):
                raise ValueError("Position error mismatch")
            k, ext = camera_matrices(predicted["request"]["settings"])
            projections = []
            for point, declared in [
                (pp, c["predicted_projection"]),
                (ap, c["observed_projection"]),
            ]:
                camera = ext @ np.array([*point, 1])
                pixel = k @ camera[:3]
                uv = (pixel[:2] / pixel[2]).tolist()
                if not np.allclose(uv, declared["uv"], atol=1e-8, rtol=0) or not math.isclose(
                    camera[2], declared["axial_depth_m"], abs_tol=1e-8
                ):
                    raise ValueError("Comparison projection mismatch")
                projections.append(uv)
            if not math.isclose(c["projection_error_px"], math.dist(*projections), abs_tol=1e-8):
                raise ValueError("Projection error mismatch")
            dense_arrays = []
            for m, frame_index in [(predicted, -1), (actual, 0)]:
                f = m["frames"][frame_index]
                dense_arrays.append(
                    [
                        np.load(
                            io.BytesIO(artifacts.read(f["artifacts"][name])), allow_pickle=False
                        )
                        for name in ["depth", "segmentation"]
                    ]
                )
            if c["depth_mae_m"] != float(
                np.mean(np.abs(dense_arrays[0][0] - dense_arrays[1][0]))
            ) or c["segmentation_changed_pixels"] != int(
                np.count_nonzero(dense_arrays[0][1] != dense_arrays[1][1])
            ):
                raise ValueError("Dense comparison mismatch")
    if not outcomes or len([e for e in events if e["kind"] == "visual_comparison"]) != len(
        outcomes
    ):
        raise ValueError("Missing executed comparison")
    return {
        "run_id": run_id,
        "passed": True,
        "manifests": checks,
        "executed_candidates": len(outcomes),
        "rendered_candidates": len(visuals),
        "unexecuted_candidates": len(set(visuals) - set(outcomes)),
    }


def audit_postprocessing(store, artifacts, run_id):
    """Audit isolation and diagnostic bindings; does not certify missing visual outputs."""
    events = store.read_events(run_id)
    run = store.get_run(run_id)
    finished = [e for e in events if e["kind"] == "finished"]
    if (
        len(finished) != 1
        or not run
        or finished[0]["data"] != run["result"]
        or run["status"] != run["result"]["status"]
    ):
        raise ValueError("Postprocessing changed or lacks a finished Core result")
    boundary = finished[0]["seq"]
    by_seq = {e["seq"]: e for e in events}
    visuals = {
        e["data"]["node_id"]: e["data"]["artifact"]
        for e in events
        if e["kind"] == "visual_prediction"
    }
    outcomes = {e["data"]["node_id"]: e for e in events if e["kind"] == "outcome"}
    rows = {r["prediction_id"]: r for r in store.error_rows()}
    for node_id, outcome in outcomes.items():
        obs = Observation.model_validate(outcome["data"]["observation"])
        record = store.execution_record(obs.receipt)
        if (
            record["status"] != "complete"
            or record["run_id"] != run_id
            or record["receipt"] != obs.model_dump()
        ):
            raise ValueError("Postprocessing receipt or observation changed")
        for kind in ["observation_evidence", "comparison", "prediction_error"]:
            if not any(
                e["kind"] == kind
                and e["data"]["node_id"] == node_id
                and outcome["seq"] < e["seq"] < boundary
                for e in events
            ):
                raise ValueError("Core outcome processing did not complete before visual work")
        errors = [
            e for e in events if e["kind"] == "prediction_error" and e["data"]["node_id"] == node_id
        ]
        if not all(e["data"]["prediction_id"] in rows for e in errors):
            raise ValueError("Calibration row missing after visual processing")
        if not any(
            e["kind"] == "trust_updated" and outcome["seq"] < e["seq"] < boundary for e in events
        ):
            raise ValueError("Core trust update missing")
    failures = []
    seen = set()
    for event in events:
        if event["kind"] not in {
            "visual_postprocess_failed",
            "visual_observed_result",
            "visual_comparison",
        }:
            continue
        data = event["data"]
        # Historical synchronous records have no job_key; normal audit_run retains their contract.
        if "job_key" not in data:
            continue
        if event["seq"] <= boundary:
            raise ValueError("Postprocessing ran before Core finished")
        outcome = by_seq[data["outcome_seq"]]
        if (
            outcome["kind"] != "outcome"
            or outcome["data"]["node_id"] != data["node_id"]
            or outcome["data"]["observation"]["receipt"] != data["receipt"]
            or data["run_id"] != run_id
            or data["visual_prediction_artifact"] != visuals[data["node_id"]]
        ):
            raise ValueError("Postprocessing record belongs to another outcome/candidate")
        binding = {
            k: data[k]
            for k in ["run_id", "node_id", "receipt", "outcome_seq", "visual_prediction_artifact"]
        }
        if identity(binding) != data["job_key"]:
            raise ValueError("Postprocessing job identity mismatch")
        if event["kind"] == "visual_postprocess_failed":
            if (
                not data["exception_type"]
                or not isinstance(data["retryable"], bool)
                or not isinstance(data["missing_artifacts"], list)
            ):
                raise ValueError("Incomplete visual failure diagnostics")
            failures.append(data)
        else:
            key = (event["kind"], data["receipt"])
            if key in seen:
                raise ValueError("Duplicate successful postprocessing record")
            seen.add(key)
            artifacts.read(
                data["manifest"] if event["kind"] == "visual_observed_result" else data["artifact"]
            )
    return {
        "run_id": run_id,
        "core_preserved": True,
        "failures": failures,
        "successful_records": len(seen),
        "executed_candidates": len(outcomes),
    }
