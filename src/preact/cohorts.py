"""Frozen local task-cohort protocol. No live sponsor or general-agent claims."""

import json
import platform
import statistics
import time
from pathlib import Path

from preact.benchmarks import metrics, paired_analysis
from preact.core.calibration import Calibration
from preact.core.models import Policy, identity
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.datasets.programs import split_programs
from preact.datasets.scenes import split_scenes
from preact.domains.program import ProgramWorld
from preact.domains.scene import SceneWorld
from preact.engines.local import LocalHeuristic, LocalVerifier

SPLITS = ("development", "calibration", "held_out")
POLICIES = {
    "direct": Policy(calibration=False),
    "preact": Policy(),
    "flat": Policy(search=False),
    "fixed": Policy(adaptive=False),
    "frozen": Policy(calibration=False),
    "single": Policy(),
    "no_disagreement": Policy(disagreement_threshold=1),
    "online": Policy(),
}


def source_hash():
    package = Path(__file__).parent
    return identity(
        {str(p.relative_to(package)): p.read_text() for p in sorted(package.rglob("*.py"))}
    )


def cases(domain, split):
    return split_programs(split) if domain == "software" else split_scenes(split)


def fixtures():
    return {
        domain: {
            split: [
                {"id": c.name, "family": c.family, "hash": c.fingerprint}
                for c in cases(domain, split)
            ]
            for split in SPLITS
        }
        for domain in ("software", "physical")
    }


def effective_policies(max_seconds=None):
    if max_seconds is not None and not 0 < max_seconds <= 3600:
        raise ValueError("Episode budget must be positive and at most 3600 seconds")
    return {
        k: v.model_copy(update={"max_seconds": max_seconds}) if max_seconds else v
        for k, v in POLICIES.items()
    }


def freeze(path, seeds=5, calibration=None, model_profile=None, max_seconds=None):
    """Exclusive creation; task/source/policy changes require a new protocol."""
    if not 1 <= seeds <= 100:
        raise ValueError("Seeds must be between 1 and 100")
    prior = json.loads(Path(calibration).read_text()) if calibration else None
    if prior and (prior["source_hash"] != source_hash() or prior["split"] != "calibration"):
        raise ValueError("Calibration must come from this source and the calibration split")
    if prior and prior.get("model_profile") != model_profile:
        raise ValueError("Calibration must use the same measured model profile")
    if prior:
        expected = {
            (domain, case.name, seed)
            for domain in ("software", "physical")
            for case in cases(domain, "calibration")
            for seed in range(seeds)
        }
        observed = [(r["domain"], r["task_id"], r["seed"]) for r in prior["episodes"]]
        if len(observed) != len(expected) or set(observed) != expected:
            raise ValueError("Calibration cohort is incomplete or contains other tasks/seeds")
        if any(r["variant"] != "direct" for r in prior["episodes"]):
            raise ValueError("Calibration must use the independent chosen-action condition")
        valid_tasks = {(domain, name) for domain, name, _ in expected}
        for error in prior["errors"]:
            context = json.loads(error["context"])
            if (context["domain"], context["task"]) not in valid_tasks:
                raise ValueError("Calibration labels include tasks outside the calibration cohort")
    import mujoco

    manifest = {
        "schema_version": 1,
        "source_hash": source_hash(),
        "fixtures": fixtures(),
        "policies": {k: v.model_dump() for k, v in effective_policies(max_seconds).items()},
        "seeds": list(range(seeds)),
        "python": platform.python_version(),
        "mujoco": mujoco.__version__,
        "platform": platform.platform(),
        "lock_hash": identity(Path("uv.lock").read_text()),
        "calibration_hash": identity(prior) if prior else None,
        "scope": "Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation",
        "analysis": "Task/seed pairs; 2000 task-cluster bootstrap draws, seed 1729; abstention is failure; frozen calibration primary; online separate",
        "limitations": [
            "Authored task families, not independently sourced repositories or robot hardware",
            "Program oracle sampling is reproducible but not a security boundary for arbitrary code",
            "MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation",
            "Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol",
            "Zero local API cost excludes CPU and operational infrastructure cost",
        ],
    }
    if max_seconds is not None:
        manifest["max_episode_seconds"] = max_seconds
    if model_profile is not None:
        manifest["model_profile"] = model_profile
        manifest["scope"] = (
            "Actual local NVIDIA model ranking and inference; bounded first-party program patches and Cartesian MuJoCo scene instances; no live Nebius/Cosmos/Isaac validation"
        )
        manifest["limitations"][3] = (
            "Direct and PreAct use identical canonical model inputs, candidate spaces, model/sampling versions and resource ceilings; actual GPU scheduling may remain nondeterministic"
        )
        manifest["limitations"][4] = (
            "No paid API request; hardware, energy and allocated infrastructure cost unmeasured, never reported as known zero"
        )
        manifest["limitations"].append(
            "Existing authored task families were previously evaluated with heuristics; this is a frozen model evaluation, not newly sourced unseen tasks"
        )
    manifest["protocol_hash"] = identity(manifest)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("x") as output:
        json.dump(manifest, output, indent=2)
    return manifest


def validate_manifest(manifest, calibration):
    import mujoco

    declared = dict(manifest)
    digest = declared.pop("protocol_hash")
    if identity(declared) != digest or manifest["source_hash"] != source_hash():
        raise ValueError("Protocol checksum/source mismatch; never resume against changed source")
    if manifest["fixtures"] != fixtures():
        raise ValueError("Task fixtures changed after freeze")
    if manifest["policies"] != {
        k: v.model_dump()
        for k, v in effective_policies(manifest.get("max_episode_seconds")).items()
    }:
        raise ValueError("Policy changed after freeze")
    if (
        manifest["python"] != platform.python_version()
        or manifest["mujoco"] != mujoco.__version__
        or manifest["lock_hash"] != identity(Path("uv.lock").read_text())
    ):
        raise ValueError("Evaluation runtime/lock differs from the frozen protocol")
    if (identity(calibration) if calibration else None) != manifest["calibration_hash"]:
        raise ValueError("Frozen calibration checksum mismatch")
    if calibration and calibration.get("model_profile") != manifest.get("model_profile"):
        raise ValueError("Frozen calibration model identity mismatch")


def load_prior(store, rows, domain):
    for row in rows:
        if json.loads(row["context"])["domain"] == domain:
            store.record_error(
                row["prediction_id"],
                row["engine"],
                row["context"],
                row["value"],
                row["label"],
                row["risk_value"],
                row["risk_label"],
            )


async def episode(
    spec,
    domain,
    seed,
    variant,
    output,
    prior_store=None,
    store=None,
    model_profile=None,
    policies=None,
):
    world = (
        ProgramWorld(
            spec, seed, evaluation_seed=int(identity({"task": spec.name, "seed": seed})[:8], 16)
        )
        if domain == "software"
        else SceneWorld(spec, seed)
    )
    reasoner = None
    engines = [LocalHeuristic(world), LocalVerifier(world)]
    if model_profile is not None:
        from preact.engines.llamacpp import LocalNemotron
        from preact.engines.reasoning import ModelProposer

        reasoner = await LocalNemotron.connect()
        actual = {
            "manifest": reasoner.manifest.public_identity,
            "capabilities": reasoner.capabilities.model_dump(),
        }
        if actual != model_profile:
            await reasoner.aclose()
            raise ValueError("Serving model differs from the frozen protocol")
        engines = [reasoner, LocalVerifier(world)]
        world = ModelProposer(world, reasoner)
    if variant == "single":
        engines = engines[:1]
    store = store or Store("sqlite:///:memory:")
    runtime = Runtime(
        store,
        Artifacts(str(output / "artifacts")),
        Registry(engines),
        (policies or POLICIES)[variant],
    )
    if prior_store is not None and variant != "online":
        runtime.calibration = Calibration(prior_store)
    started = time.monotonic()
    try:
        result = await runtime.run(
            world, direct=variant == "direct", forecast_selected=variant == "direct"
        )
    except Exception as error:
        result = {
            "success": False,
            "status": "interrupted" if store.pending_execution(runtime.run_id) else "failed",
            "calls": runtime.calls,
            "agent_calls": runtime.agent_calls,
            "execution_calls": runtime.execution_calls,
            "cost_usd": runtime.cost,
            "cost_known": False,
            "latency_ms": (time.monotonic() - started) * 1000,
            "infrastructure_error": type(error).__name__,
        }
    finally:
        if reasoner is not None:
            await reasoner.aclose()
    events = store.read_events(runtime.run_id)
    evaluations = [
        e["data"]["node"]["evaluation"]
        for e in events
        if e["kind"] == "node_updated" and e["data"]["node"].get("evaluation")
    ]
    outcomes = [e for e in events if e["kind"] == "outcome"]
    intents = sum(e["kind"] == "execution_intent" for e in events)
    aborted = sum(e["kind"] == "execution_aborted" for e in events)
    row = {
        **result,
        "domain": domain,
        "task_id": spec.name,
        "family": spec.family,
        "fixture_hash": spec.fingerprint,
        "seed": seed,
        "variant": variant,
        "run_id": runtime.run_id,
        "steps": len(outcomes),
        "execution_attempts": intents - aborted,
        "aborted_before_dispatch": aborted,
        "unsafe": any(e["data"]["observation"]["unsafe"] for e in outcomes),
        "unsafe_actions": sum(e["data"]["observation"]["unsafe"] for e in outcomes),
        "unsafe_observation_known": intents - aborted == len(outcomes),
        "comparisons": [e["data"] for e in events if e["kind"] == "comparison"],
        "simulation_calls": sum(
            e["data"]["prediction"]["sample_count"]
            for e in events
            if e["kind"] == "prediction"
            and not e["data"]["cached"]
            and e["data"]["prediction"]["evidence"] == "simulation"
        ),
        "disagreement": statistics.mean(e["disagreement"] for e in evaluations)
        if evaluations
        else 0,
    }
    labels = {e["data"]["prediction_id"] for e in events if e["kind"] == "prediction_error"}
    errors = [e for e in store.error_rows() if e["prediction_id"] in labels]
    archive = {
        "task": world.task.model_dump(),
        "policy": (policies or POLICIES)[variant].model_dump(),
        "events": events,
        "result": row,
        "errors": errors,
    }
    # Caller owns shared stores; isolated stores can release connections immediately.
    if variant != "online":
        store.db.dispose()
    return archive


async def evaluate_cohort(
    manifest_path,
    output,
    split="held_out",
    calibration_path=None,
    ablations=False,
    domains=("software", "physical"),
):
    manifest = json.loads(Path(manifest_path).read_text())
    calibration = json.loads(Path(calibration_path).read_text()) if calibration_path else None
    validate_manifest(manifest, calibration)
    if split == "held_out" and calibration is None:
        raise ValueError("Held-out primary evaluation requires measured frozen calibration")
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    variants = list(POLICIES) if ablations else ["direct", "preact"]
    if split == "calibration":
        variants = ["direct"]  # Independent selected-action forecasts with actual labels.
    rows, errors, all_errors = [], {}, []
    for domain in domains:
        prior = Store("sqlite:///:memory:")
        load_prior(prior, calibration["errors"] if calibration else [], domain)
        online = Store("sqlite:///:memory:")
        load_prior(online, calibration["errors"] if calibration else [], domain)
        for spec in cases(domain, split):
            for seed in manifest["seeds"]:
                for variant in variants:
                    path = root / f"{domain}-{spec.name}-{variant}-{seed}.json"
                    if path.exists():
                        archive = json.loads(path.read_text())
                        if archive.get("protocol_hash") != manifest["protocol_hash"]:
                            raise ValueError("Existing episode belongs to a different protocol")
                        if variant == "online":
                            load_prior(online, archive["errors"], domain)
                    else:
                        validate_manifest(manifest, calibration)
                        archive = await episode(
                            spec,
                            domain,
                            seed,
                            variant,
                            root,
                            prior,
                            online if variant == "online" else None,
                            manifest.get("model_profile"),
                            effective_policies(manifest.get("max_episode_seconds")),
                        )
                        archive["protocol_hash"] = manifest["protocol_hash"]
                        with path.open("x") as file:
                            json.dump(archive, file)
                    rows.append(archive["result"])
                    errors.setdefault((domain, variant), []).extend(archive["errors"])
                    all_errors.extend(archive["errors"])
        prior.db.dispose()
        online.db.dispose()
    validate_manifest(manifest, calibration)
    report = {
        "protocol_hash": manifest["protocol_hash"],
        "source_hash": source_hash(),
        "scope": manifest["scope"],
        "split": split,
        "limitations": manifest["limitations"],
        "metrics": {
            f"{domain}/{variant}": metrics(
                [r for r in rows if r["domain"] == domain and r["variant"] == variant], labels
            )
            for (domain, variant), labels in errors.items()
        },
        "paired_analysis": paired_analysis(rows),
        "episodes": rows,
        "errors": all_errors,
    }
    if manifest.get("model_profile") is not None:
        report["model_profile"] = manifest["model_profile"]
    (root / "report.json").write_text(json.dumps(report, indent=2))
    table = (
        f"# {split.replace('_', ' ').title()} Local Cohort Evaluation\n\n{manifest['scope']}.\n\n"
    )
    table += "| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |\n|---|---:|---:|---:|---:|---:|---:|\n"
    for key, m in report["metrics"].items():
        unsafe = (
            f"{m['unsafe_episode_rate']:.1%}" if m["unsafe_episode_rate"] is not None else "Unknown"
        )
        table += f"| {key} | {m['n']} | {m['success_rate']:.1%} | {unsafe} | {m['abstention_rate']:.1%} | {m['mean_calls']:.1f} | {m['latency_p50_ms']:.1f} |\n"
    table += (
        "\nProtocol: `"
        + manifest["protocol_hash"]
        + "`. Report JSON retains every episode and task-cluster paired intervals.\n\n"
    )
    table += "\n".join("- " + limitation for limitation in manifest["limitations"]) + "\n"
    (root / "report.md").write_text(table)
    return report
