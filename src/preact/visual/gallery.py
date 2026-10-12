"""Standalone gallery assembled exclusively from hash-verified saved records."""

import base64
import html
import struct
import zlib

import numpy as np

from preact.visual.adapter import dense, manifest


def png(array):
    h, w, channels = array.shape
    if channels != 3:
        raise ValueError("RGB preview required")

    def chunk(name, data):
        return (
            struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
        )

    pixels = b"".join(b"\0" + row.tobytes() for row in array.astype(np.uint8))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def image(data, label):
    encoded = base64.b64encode(data).decode()
    return f'<figure><img src="data:image/png;base64,{encoded}" alt="{html.escape(label)}"><figcaption>{html.escape(label)}</figcaption></figure>'


def frames(artifacts, digest):
    m = manifest(artifacts, digest)
    sections = []
    for f in m["frames"]:
        rgb = artifacts.read(f["artifacts"]["rgb"])
        depth = dense(artifacts, f, "depth")
        ids = dense(artifacts, f, "segmentation")
        gray = np.clip(depth / 4 * 255, 0, 255).astype(np.uint8)
        colors = np.array(
            [
                [0, 0, 0],
                [100, 110, 120],
                [20, 110, 240],
                [20, 200, 70],
                [220, 220, 230],
                [230, 50, 30],
            ]
            + [[240, 140, 40]] * 95,
            dtype=np.uint8,
        )
        sections.append(
            f'<div class="frame"><b>{html.escape(f["phase"])} · {f["kind"]}</b>'
            f'<div class="modalities">{image(rgb, "RGB — Cycles CPU")}'
            f"{image(png(np.stack([gray] * 3, axis=-1)), 'Depth — axial Z, 0–4 m preview')}"
            f"{image(png(colors[ids]), 'Segmentation — ID 0 background, 1 floor, 2 cube, 3 target, 4 gripper, 5+ obstacles')}</div></div>"
        )
    return "".join(sections)


def build_gallery(store, artifacts, run_id, target):
    events = store.read_events(run_id)
    nodes = {e["data"]["node"]["id"]: e["data"]["node"] for e in events if e["kind"] == "node"}
    selected = {
        e["data"]["node_id"]
        for e in events
        if e["kind"] == "decision" and e["data"].get("decision") == "execute"
    }
    outcomes = {
        e["data"]["node_id"]: e["data"]["observation"] for e in events if e["kind"] == "outcome"
    }
    parts = [
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>PreAct Blender visual world</title>',
        "<style>body{background:#111923;color:#e5edf5;font:15px system-ui;margin:32px}section{border-top:1px solid #526072;padding:20px 0}img{width:288px;image-rendering:auto}figure{margin:8px;max-width:288px}figcaption{font-size:12px}.modalities{display:flex;flex-wrap:wrap}.frame{margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}a{color:#8ec7ff}</style>",
        "<h1>PreAct · Blender visual world</h1><p>All images are synthetic. Futures are hypothetical MuJoCo predictions. Only complete Runtime receipts have actual outcome labels. Images provide no safety authority.</p>",
        f"<p>Run {html.escape(run_id)} · source records: events.json, ledger.db, artifacts/</p>",
    ]
    for event in events:
        d = event["data"]
        if event["kind"] == "visual_current":
            parts.append(
                "<section><h2>Current observed scene · synthetic representation</h2>"
                + frames(artifacts, d["manifest"])
                + "</section>"
            )
        elif event["kind"] == "visual_prediction":
            node = nodes[d["node_id"]]
            visual = d["prediction"]
            m = manifest(artifacts, visual["artifacts"]["manifest"])
            source = manifest(artifacts, m["provenance"]["mujoco_prediction"])
            label = (
                "SELECTED / EXECUTED"
                if d["node_id"] in outcomes
                else "SELECTED"
                if d["node_id"] in selected
                else "UNEXECUTED — outcome unavailable"
            )
            trajectory = source["raw"]["trajectory"]
            points = " ".join(
                f"{(p[0] + 0.8) * 300:.2f},{(0.8 - p[1]) * 200:.2f}" for p in trajectory
            )
            parts.append(
                f"<section><h2>{html.escape(node['action']['name'])} · {label}</h2>"
                f"<p>MuJoCo checks: {html.escape(str(source['mandatory_checks']))}</p>"
                f'<svg viewBox="0 0 480 320" width="288" aria-label="Actual retained MuJoCo trajectory in XY"><rect width="480" height="320" fill="#253246"/><polyline points="{points}" stroke="#82c5ff" fill="none" stroke-width="2"/></svg>'
                "<p>Retained MuJoCo trajectory · XY metres (viewport −0.8 to +0.8)</p>"
                + frames(artifacts, visual["artifacts"]["manifest"])
                + f"<details><summary>Full source trajectory, camera and provenance</summary><pre>{html.escape(str({'trajectory_m': trajectory, 'camera': m['request']['settings'], 'provenance': m['provenance']}))}</pre></details></section>"
            )
        elif event["kind"] == "visual_observed_result":
            parts.append(
                "<section><h2>Executed observation · synthetic representation</h2>"
                f"<p>Complete receipt: {html.escape(d['receipt'])}</p>"
                + frames(artifacts, d["manifest"])
                + "</section>"
            )
        elif event["kind"] == "visual_comparison":
            c = manifest(artifacts, d["artifact"])
            shown = {
                k: c[k]
                for k in (
                    "predicted_position_m",
                    "observed_position_m",
                    "position_error_m",
                    "projection_error_px",
                    "depth_mae_m",
                    "segmentation_changed_pixels",
                    "rgb_equal",
                    "interpretation",
                )
            }
            parts.append(
                "<section><h2>Prediction versus committed outcome</h2><pre>"
                + html.escape(str(shown))
                + "</pre></section>"
            )
    parts.append("</html>")
    target.write_text("".join(parts))
