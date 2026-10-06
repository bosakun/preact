import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="PreAct counterfactual runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    demo = sub.add_parser("demo")
    demo.add_argument("domain", choices=["software", "physical"])
    demo.add_argument("--direct", action="store_true")
    demo.add_argument("--seed", type=int, default=0)
    demo.add_argument("--task", choices=["default", "release"], default="default")
    bench = sub.add_parser("benchmark")
    bench.add_argument("--output", default="reports/local-benchmark")
    bench.add_argument("--seeds", type=int, default=5)
    bench.add_argument("--ablations", action="store_true")
    bench.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="Equal episode time budget for every compared condition (default 120)",
    )
    bench.add_argument(
        "--workflows",
        action="store_true",
        help="Include the trusted migration/configuration/build task",
    )
    sub.add_parser("doctor")
    live = sub.add_parser(
        "validate-token-factory",
        help="Discover admitted models and validate real structured inference",
    )
    live.add_argument("--output", required=True)
    live.add_argument("--catalog-only", action="store_true")
    replay = sub.add_parser("replay", help="Import recorded benchmark evidence without executing")
    replay.add_argument("archive")
    replay.add_argument("--artifact-source")
    freeze = sub.add_parser("freeze-cohort", help="Freeze local task/source/policy protocol")
    freeze.add_argument("manifest")
    freeze.add_argument("--seeds", type=int, default=5)
    freeze.add_argument("--calibration")
    freeze.add_argument("--engine", choices=["local", "local-model"], default="local")
    freeze.add_argument("--max-seconds", type=float, default=None)
    cohort = sub.add_parser("evaluate-cohort", help="Run frozen local cohort comparisons")
    cohort.add_argument("manifest")
    cohort.add_argument("--output", required=True)
    cohort.add_argument(
        "--split", choices=["development", "calibration", "held_out"], default="held_out"
    )
    cohort.add_argument("--calibration")
    cohort.add_argument("--ablations", action="store_true")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        uvicorn.run("preact.service.app:app", host=args.host, port=args.port)
    elif args.command == "doctor":
        print(
            json.dumps(
                {
                    "mode": os.getenv("PREACT_MODE", "local"),
                    "mujoco_installed": bool(importlib.util.find_spec("mujoco")),
                    "contree_installed": bool(importlib.util.find_spec("contree_sdk")),
                    "configured": {
                        name: bool(os.getenv(name))
                        for name in [
                            "NEBIUS_API_KEY",
                            "NEBIUS_MODEL",
                            "CONTREE_API_KEY",
                            "CONTREE_IMAGE",
                            "ISAAC_ENDPOINT",
                            "COSMOS_ENDPOINT",
                            "WORKER_TOKEN",
                            "PREACT_LOCAL_MODEL_MANIFEST",
                            "PREACT_LOCAL_MODEL_KEY_FILE",
                        ]
                    },
                    "live_validation": "not implied by configuration",
                },
                indent=2,
            )
        )
    elif args.command == "validate-token-factory":
        from preact.integration_validation import validate_token_factory

        output = Path(args.output)
        if output.exists():
            parser.error("Evidence already exists; choose a new output path")
        report = asyncio.run(
            validate_token_factory(
                os.getenv("NEBIUS_MODEL"),
                os.getenv("NEBIUS_API_KEY"),
                catalog_only=args.catalog_only,
            )
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x") as evidence:
            json.dump(report, evidence, indent=2)
        print(json.dumps({"status": report["status"], "evidence": str(output)}))
        if report["status"] in {"failed", "blocked"}:
            raise SystemExit(1)
    elif args.command == "freeze-cohort":
        from preact.cohorts import freeze

        profile = None
        if args.engine == "local-model":

            async def model_profile():
                from preact.engines.llamacpp import LocalNemotron

                engine = await LocalNemotron.connect()
                try:
                    return {
                        "manifest": engine.manifest.public_identity,
                        "capabilities": engine.capabilities.model_dump(),
                    }
                finally:
                    await engine.aclose()

            profile = asyncio.run(model_profile())
        manifest = freeze(args.manifest, args.seeds, args.calibration, profile, args.max_seconds)
        print(json.dumps({"protocol_hash": manifest["protocol_hash"]}, indent=2))
    elif args.command == "evaluate-cohort":
        from preact.cohorts import evaluate_cohort

        report = asyncio.run(
            evaluate_cohort(
                args.manifest, args.output, args.split, args.calibration, args.ablations
            )
        )
        print(json.dumps(report["metrics"], indent=2))
    elif args.command == "replay":
        from preact.core.store import Store
        from preact.engines.storage import artifact_store
        from preact.replay import import_archive

        run_id = import_archive(
            args.archive,
            Store(os.getenv("PREACT_DATABASE_URL", "sqlite:///.preact/preact.db")),
            artifact_store(),
            args.artifact_source,
        )
        print(json.dumps({"run_id": run_id, "status": "recorded", "executed": False}, indent=2))
    elif args.command == "benchmark":
        if args.seeds < 1 or args.seeds > 100:
            parser.error("seeds must be between 1 and 100")
        if args.max_seconds is not None and not 0 < args.max_seconds <= 3600:
            parser.error("max-seconds must be positive and at most 3600")
        from preact.benchmarks import benchmark

        report = asyncio.run(
            benchmark(
                args.output,
                args.seeds,
                os.getenv("PREACT_MODE", "local"),
                args.ablations,
                args.workflows,
                args.max_seconds,
            )
        )
        print(json.dumps(report["metrics"], indent=2))
    else:

        async def run():
            from preact.core.runtime import Runtime
            from preact.core.store import Store
            from preact.engines.storage import artifact_store
            from preact.service.app import component_scope

            async with component_scope(
                args.domain, args.seed, os.getenv("PREACT_MODE", "local"), args.task
            ) as (world, registry):
                runtime = Runtime(
                    Store(os.getenv("PREACT_DATABASE_URL", "sqlite:///.preact/preact.db")),
                    artifact_store(),
                    registry,
                )
                result = await runtime.run(world, direct=args.direct)
                print(json.dumps({"run_id": runtime.run_id, **result}, indent=2))

        asyncio.run(run())


if __name__ == "__main__":
    main()
