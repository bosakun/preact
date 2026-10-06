"""Audit archived prediction labels against executed outcomes without running agents."""

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from preact.core.models import identity


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit_archive(archive):
    events = archive["events"]
    row = archive["result"]
    sequences = [e["seq"] for e in events]
    require(sequences == sorted(set(sequences)), "Event ordering/uniqueness mismatch")
    require(all(e["run_id"] == row["run_id"] for e in events), "Run identity mismatch")
    nodes, predictions, intents, outcomes, labels = {}, {}, {}, {}, {}
    comparisons = set()
    for event in events:
        kind, data = event["kind"], event["data"]
        if kind in {"node", "node_updated"}:
            nodes[data["node"]["id"]] = data["node"]
        elif kind == "prediction":
            prediction = data["prediction"]
            key = (data["node_id"], prediction["id"])
            if key in predictions:
                require(predictions[key] == prediction, "Cached prediction identity changed")
            predictions[key] = prediction
        elif kind == "execution_intent":
            require(data["node_id"] not in intents, "Duplicate execution intent")
            intents[data["node_id"]] = event
        elif kind == "outcome":
            require(data["node_id"] not in outcomes, "Duplicate observed outcome")
            outcomes[data["node_id"]] = event
        elif kind == "comparison":
            comparisons.add((data["node_id"], data["prediction_id"]))
        elif kind == "prediction_error":
            require(data["prediction_id"] not in labels, "Duplicate prediction error")
            labels[data["prediction_id"]] = event
    for node_id, event in outcomes.items():
        require(node_id in intents, "Outcome without durable execution intent")
        node = nodes[node_id]
        require(
            node["kind"] == "action" and node["state"]["kind"] == "observed",
            "Hypothetical action committed",
        )
        if row["variant"] == "direct":
            require(node["parent_id"] is None, "Direct action has a search ancestor")
        else:
            parent = nodes[node["parent_id"]]
            require(
                parent["kind"] == "state"
                and parent["parent_id"] is None
                and parent["state"]["id"] == node["state"]["id"],
                "Lookahead action committed instead of a first action",
            )
        intent = intents[node_id]
        observation = event["data"]["observation"]
        require(intent["seq"] < event["seq"], "Outcome precedes execution intent")
        require(observation["receipt"] == intent["data"]["receipt"], "Receipt mismatch")
        require(observation["state"]["kind"] == "observed", "Hypothesis used as observation")
        require(observation["state"]["domain"] == row["domain"], "Observation domain mismatch")
    for key in comparisons:
        require(key in predictions and key[0] in outcomes, "Unexecuted/missing comparison")
    errors = archive["errors"]
    ids = [e["prediction_id"] for e in errors]
    require(len(ids) == len(set(ids)) and set(ids) == set(labels), "Ledger/event label mismatch")
    expected_comparisons, expected_labels = set(), set()
    for key, prediction in predictions.items():
        if key[0] not in outcomes or prediction["horizon"] != 1:
            continue
        expected_comparisons.add(key)
        observation = outcomes[key[0]]["data"]["observation"]
        if (
            prediction["success"]["value"] is not None
            and prediction["success_metric"] == observation["success_metric"]
            and prediction["risk_metric"] == observation["risk_metric"]
        ):
            expected_labels.add(prediction["id"])
    require(comparisons == expected_comparisons, "Missing/unaligned executed comparison")
    require(set(ids) == expected_labels, "Missing/unaligned known executed prediction label")
    for error in errors:
        event = labels[error["prediction_id"]]
        data = event["data"]
        key = (data["node_id"], error["prediction_id"])
        require(key in predictions and key[0] in outcomes, "Unexecuted prediction labeled")
        prediction, node = predictions[key], nodes[key[0]]
        outcome = outcomes[key[0]]
        observation = outcome["data"]["observation"]
        require(outcome["seq"] < event["seq"], "Label precedes observed outcome")
        require(key in comparisons, "Missing aligned comparison")
        require(prediction["horizon"] == 1, "Multi-step prediction given first-action label")
        require(prediction["state_id"] == node["state"]["id"], "Input state mismatch")
        require(prediction["action_ids"] == [node["action"]["id"]], "Action mismatch")
        require(node["action"]["state_id"] == node["state"]["id"], "Action/state mismatch")
        require(error["source"] == "execution", "Non-execution ledger label")
        require(
            error["engine"] == prediction["engine_id"] + "@" + prediction["engine_version"],
            "Engine/version mismatch",
        )
        context = json.loads(error["context"])
        require(
            context["domain"] == row["domain"]
            and context["task"] == archive["task"]["id"]
            and context["horizon"] == 1
            and context["truth_source"] == observation["state"]["provenance"],
            "Reliability context mismatch",
        )
        for metric in ("success_metric", "risk_metric"):
            require(
                context[metric] == prediction[metric] == observation[metric],
                "Claim version mismatch",
            )
        actual = int(observation["checks"].get("action_success", observation["success"]))
        value = prediction["success"]["value"]
        require(error["value"] == value == data["predicted"], "Prediction value mismatch")
        require(error["label"] == actual == data["observed"], "Success label mismatch")
        require(error["risk_label"] == int(observation["unsafe"]), "Risk label mismatch")
        require(error["risk_value"] == prediction["risk"]["value"], "Risk value mismatch")
        require(math.isclose(data["brier"], (value - actual) ** 2, abs_tol=1e-12), "Brier mismatch")
    return {
        "predictions": len(predictions),
        "executed_actions": len(outcomes),
        "aligned_error_rows": len(errors),
        "unexecuted_predictions_without_observed_labels": sum(
            node_id not in outcomes for node_id, _ in predictions
        ),
    }


def audit(protocol_path, directory, allow_partial=False, split="held_out"):
    manifest = json.loads(protocol_path.read_text())
    declared = dict(manifest)
    digest = declared.pop("protocol_hash")
    require(identity(declared) == digest, "Protocol digest mismatch")
    variants = ["direct"] if split == "calibration" else list(manifest["policies"])
    expected = {
        (domain, case["id"], seed, variant): case["hash"]
        for domain, splits in manifest["fixtures"].items()
        for case in splits[split]
        for seed in manifest["seeds"]
        for variant in variants
    }
    seen, totals, checksums = set(), Counter(), {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "report.json":
            continue
        content = path.read_bytes()
        archive = json.loads(content)
        row = archive["result"]
        key = (row["domain"], row["task_id"], row["seed"], row["variant"])
        require(key in expected and key not in seen, "Unexpected/duplicate episode")
        require(row["fixture_hash"] == expected[key], "Fixture mismatch")
        require(archive["protocol_hash"] == digest, "Episode protocol mismatch")
        require(archive["policy"] == manifest["policies"][row["variant"]], "Policy mismatch")
        totals.update(audit_archive(archive))
        checksums[path.name] = hashlib.sha256(content).hexdigest()
        seen.add(key)
    require(allow_partial or seen == set(expected), "Cohort incomplete")
    return {
        "status": "complete-cohort-alignment-audited"
        if seen == set(expected)
        else "partial-snapshot",
        "scope": "Archived execution/outcome/error alignment only; no new inference or integration certification",
        "protocol_hash": digest,
        "source_hash": manifest["source_hash"],
        "split": split,
        "audited_episodes": len(seen),
        "expected_episodes": len(expected),
        "counts": dict(totals),
        "archive_sha256": checksums,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument(
        "--split", choices=["calibration", "held_out", "development"], default="held_out"
    )
    args = parser.parse_args()
    result = audit(args.protocol, args.directory, args.allow_partial, args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "archive_sha256"}))
