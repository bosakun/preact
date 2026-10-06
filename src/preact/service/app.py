from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from preact.core.io import durable_io
from preact.core.models import Contract, Policy, uid
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.physical import PhysicalWorld
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


class RunRequest(Contract):
    domain: Literal["software", "physical"]
    mode: Literal["preact", "direct"] = "preact"
    seed: int = Field(default=0, ge=0, le=100000)
    policy: Policy = Field(default_factory=Policy)
    task: Literal["default", "release"] = "default"

    @model_validator(mode="after")
    def compatible_task(self):
        if self.domain != "software" and self.task != "default":
            raise ValueError("Release workflow belongs to Software World")
        return self


async def components(domain: str, seed: int, mode: str, task: str = "default", *, stack=None):
    def track(resource):
        if stack is not None:
            stack.push_async_callback(resource.aclose)
        return resource

    world = SoftwareWorld(seed) if domain == "software" else PhysicalWorld(seed)
    if task == "release":
        if domain != "software":
            raise ValueError("Release workflow belongs to Software World")
        from preact.domains.repository import RepositoryWorld

        world = RepositoryWorld(seed)
    if mode == "local":
        return world, Registry([LocalHeuristic(world), LocalVerifier(world)])
    if mode == "local-model":
        from preact.engines.llamacpp import LocalNemotron
        from preact.engines.reasoning import ModelProposer

        reasoner = track(await LocalNemotron.connect())
        return ModelProposer(world, reasoner), Registry([reasoner, LocalVerifier(world)])
    if mode != "cloud":
        raise ValueError("Unknown runtime mode")
    from preact.engines.nebius import ModelProposer, Nemotron
    from preact.engines.remote import RemoteEngine
    from preact.engines.sandbox import Sandbox

    cheap = track(Nemotron(os.getenv("NEBIUS_MODEL", "")))
    engines = [cheap]
    if os.getenv("NEBIUS_STRONG_MODEL"):
        engines.append(track(Nemotron(os.environ["NEBIUS_STRONG_MODEL"], tier=1)))
    if domain == "software":
        # A cloud verifier cannot turn a local executor into a hosted software world.
        from preact.domains.cloud import SandboxRepositoryWorld, SandboxWorld

        world = SandboxRepositoryWorld(seed) if task == "release" else SandboxWorld(seed)
        if task != "release":
            world.proposer = cheap
        verifier = track(Sandbox(world))
        world.sandbox = verifier
        engines.append(verifier)
    else:
        from preact.domains.cloud import IsaacWorld

        world = track(await IsaacWorld.connect(seed))
        for name in ["COSMOS_ENDPOINT", "ISAAC_ENDPOINT", "ISAAC_STRONG_ENDPOINT"]:
            if os.getenv(name):
                engines.append(
                    track(
                        await RemoteEngine.connect(os.environ[name], os.getenv("WORKER_TOKEN", ""))
                    )
                )
    return ModelProposer(world, cheap), Registry(engines)


@asynccontextmanager
async def component_scope(domain: str, seed: int, mode: str, task: str = "default"):
    """Close owned cloud clients on completion, setup failure and cancellation."""
    async with AsyncExitStack() as stack:
        yield await components(domain, seed, mode, task, stack=stack)


def create_app(database_url=None, artifacts_root=None, mode=None, service_policy=None):
    load_dotenv()
    store = Store(database_url or os.getenv("PREACT_DATABASE_URL", "sqlite:///.preact/preact.db"))
    if artifacts_root:
        artifacts = Artifacts(artifacts_root)
    else:
        from preact.engines.storage import artifact_store

        artifacts = artifact_store()
    mode = mode or os.getenv("PREACT_MODE", "local")
    if mode not in {"local", "local-model", "cloud"}:
        raise ValueError("PREACT_MODE must be local, local-model or cloud")
    limits = service_policy or Policy.model_validate_json(
        os.getenv("PREACT_SERVICE_POLICY_JSON", "{}")
    )
    active: dict[str, tuple[Runtime, asyncio.Task]] = {}
    slots = asyncio.Semaphore(2)
    admission = asyncio.Lock()
    cleanup = set()
    persistence_failures = {}
    closing = False

    @asynccontextmanager
    async def lifespan(app):
        nonlocal closing
        closing = False
        await store.call(
            "recover",
        )
        try:
            yield
        finally:
            closing = True
            # Drain native admission writes before snapshotting active runs.
            async with admission:
                running = list(active.values())
            for runtime, task in running:
                runtime.cancelled = True
                task.cancel()
            await asyncio.gather(*(task for _, task in running), return_exceptions=True)
            if cleanup:
                await asyncio.gather(*list(cleanup), return_exceptions=True)

    app = FastAPI(title="PreAct", version="0.1.0", lifespan=lifespan)
    app.state.store = store

    @app.exception_handler(SQLAlchemyError)
    async def unavailable_storage(request, error):
        return JSONResponse(status_code=503, content={"detail": "Durable storage is unavailable"})

    @app.get("/api/health")
    async def health():
        if closing:
            raise HTTPException(503, "Service is shutting down")
        try:
            await store.call("healthy")
            # Only reconcile terminated tasks after all their I/O has drained.
            # Never retry an action or invent an outcome from a storage failure.
            for run_id, failure in list(persistence_failures.items()):
                status = (
                    "interrupted" if await store.call("pending_execution", run_id) else "failed"
                )
                await store.call(
                    "set_status", run_id, status, {"error": failure, "cost_known": False}
                )
                await store.call("append", run_id, "failed", {"error": failure, "status": status})
                persistence_failures.pop(run_id, None)
        except SQLAlchemyError:
            raise HTTPException(503, "Durable storage is unavailable") from None
        return {
            "status": "ok",
            "mode": mode,
            "evidence_scope": {
                "local": "trusted Python / real MuJoCo Cartesian simulation",
                "local-model": "local NVIDIA model reasoning / trusted Python / real MuJoCo Cartesian simulation",
                "cloud": "cloud",
            }[mode],
            "live_sponsor_validation": False,
        }  # actual evidence is retained per run, not inferred from config

    @app.get("/api/runs")
    async def runs():
        return await store.call(
            "list_runs",
        )

    @app.post("/api/runs", status_code=202)
    async def start(request: RunRequest):
        # Server-owned safety/resource ceilings apply before allocating a run or
        # calling a vendor. Clients may tighten them; benchmark ablations use Core.
        requested = Policy.model_validate(
            {
                **limits.model_dump(),
                **request.policy.model_dump(exclude_unset=True),
            }
        )
        ceilings = (
            "max_depth",
            "width",
            "max_nodes",
            "max_calls",
            "max_seconds",
            "max_cost_usd",
            "max_risk",
            "uncertainty_threshold",
            "disagreement_threshold",
        )
        violations = [name for name in ceilings if getattr(requested, name) > getattr(limits, name)]
        if requested.min_success < limits.min_success:
            violations.append("min_success")
        if limits.calibration and not requested.calibration:
            violations.append("calibration")
        if violations:
            raise HTTPException(
                422, {"reason": "Policy exceeds server limits", "fields": violations}
            )
        request.policy = requested
        async with admission:
            if closing:
                raise HTTPException(503, "Service is shutting down")
            if sum(not task.done() for _, task in active.values()) >= 4:
                raise HTTPException(429, "Run queue is full")
            run_id = uid()
            try:
                await store.call(
                    "create_run",
                    {"request": request.model_dump(), "environment": mode},
                    run_id=run_id,
                )
            except asyncio.CancelledError:
                # The request may disconnect during admission. Its drained write
                # must not leave an orphan queued run with no execution task.
                await store.call(
                    "set_status", run_id, "cancelled", {"reason": "Admission cancelled"}
                )
                raise
            if closing:
                await store.call(
                    "set_status", run_id, "cancelled", {"reason": "Shutdown during admission"}
                )
                raise HTTPException(503, "Service is shutting down")
            runtime = Runtime(store, artifacts, Registry([]), request.policy)

            async def execute():
                async with slots:
                    if runtime.cancelled:
                        await store.call(
                            "set_status", run_id, "cancelled", {"reason": "Cancelled before start"}
                        )
                        await store.call("append", run_id, "finished", {"status": "cancelled"})
                        active.pop(run_id, None)
                        return
                    try:
                        async with asyncio.timeout(request.policy.max_seconds):
                            async with component_scope(
                                request.domain, request.seed, mode, request.task
                            ) as (world, registry):
                                runtime.registry = registry
                                await runtime.run(world, run_id, request.mode == "direct")
                    except asyncio.CancelledError:
                        status = (
                            "interrupted"
                            if await store.call("pending_execution", run_id)
                            else "cancelled"
                        )
                        await store.call(
                            "set_status",
                            run_id,
                            status,
                            {"reason": "Cancellation; reconcile any pending intent"},
                        )
                        await store.call("append", run_id, "finished", {"status": status})
                    except Exception as error:
                        status = (
                            "interrupted"
                            if await store.call("pending_execution", run_id)
                            else "failed"
                        )
                        await store.call(
                            "set_status",
                            run_id,
                            status,
                            {"error": type(error).__name__, "cost_known": False},
                        )
                        await store.call(
                            "append",
                            run_id,
                            "failed",
                            {"error": type(error).__name__, "status": status},
                        )
                    finally:
                        # History lives in SQL; completed trees must not accumulate in RAM.
                        active.pop(run_id, None)

            active[run_id] = runtime, asyncio.create_task(execute())

            async def record_queued_cancellation():
                record = await store.call("get_run", run_id)
                if record and record["status"] in {"queued", "running"}:
                    status = (
                        "interrupted"
                        if await store.call("pending_execution", run_id)
                        else "cancelled"
                    )
                    await store.call(
                        "set_status", run_id, status, {"reason": "Cancelled before completion"}
                    )
                    await store.call("append", run_id, "finished", {"status": status})

            def cleanup_done(task):
                cleanup.discard(task)
                if not task.cancelled() and (error := task.exception()) is not None:
                    persistence_failures[run_id] = type(error).__name__

            def completed(task):
                active.pop(run_id, None)
                if task.cancelled():
                    background = asyncio.create_task(record_queued_cancellation())
                    cleanup.add(background)
                    background.add_done_callback(cleanup_done)
                elif (error := task.exception()) is not None:
                    persistence_failures[run_id] = type(error).__name__

            active[run_id][1].add_done_callback(completed)
            return {"id": run_id}

    async def require_run(run_id):
        record = await store.call("get_run", run_id)
        if not record:
            raise HTTPException(404, "Run not found")
        return record

    @app.get("/api/runs/{run_id}")
    async def run(run_id: str):
        return await require_run(run_id)

    @app.delete("/api/runs/{run_id}")
    async def cancel(run_id: str):
        await require_run(run_id)
        if run_id in active:
            active[run_id][0].cancelled = True
            active[run_id][1].cancel()
        return {"id": run_id, "status": "cancellation_requested"}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, after: int = Query(default=0, ge=0)):
        await require_run(run_id)
        return await store.call("read_events", run_id, after)

    @app.get("/api/runs/{run_id}/stream")
    async def stream(
        run_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None),
    ):
        await require_run(run_id)
        if last_event_id is not None:
            try:
                resumed = int(last_event_id)
                if resumed < 0:
                    raise ValueError
            except ValueError:
                raise HTTPException(422, "Invalid Last-Event-ID") from None
            after = max(after, resumed)

        async def generate():
            cursor = after
            while True:
                if await request.is_disconnected():
                    return
                for event in await store.call("read_events", run_id, cursor):
                    # A backlog may otherwise send without yielding to socket I/O.
                    await asyncio.sleep(0)
                    if await request.is_disconnected():
                        return
                    cursor = event["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                record = await store.call("get_run", run_id)
                if record["status"] not in {"queued", "running"}:
                    # Drain a final event committed between the last read and status lookup.
                    for event in await store.call("read_events", run_id, cursor):
                        await asyncio.sleep(0)
                        if await request.is_disconnected():
                            return
                        yield f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/api/ledger")
    async def ledger():
        return await store.call(
            "error_rows",
        )

    @app.get("/api/artifacts/{digest}")
    async def artifact(digest: str):
        try:
            body = await durable_io(artifacts.read, digest)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "Artifact not found") from None
        from fastapi.responses import Response

        media = (
            "video/mp4" if len(body) > 12 and body[4:8] == b"ftyp" else "application/octet-stream"
        )
        return Response(body, media_type=media, headers={"X-Content-Type-Options": "nosniff"})

    dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(dist / "index.html")

    return app


app = create_app()
