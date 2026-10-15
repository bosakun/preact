"""Bounded session control above CognitiveAgent. Core retains all execution authority."""

import asyncio
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from preact.core.io import durable_io
from preact.core.models import Policy, uid
from preact.domains.queue_playground import PRESETS, ROUNDS, cognitive_runner, prepare_queue
from preact.playground.evidence import artifact_details, read_sources
from preact.playground.models import (
    PlaygroundCommand,
    PlaygroundSession,
    PlaygroundSnapshot,
    PlaygroundStart,
)
from preact.playground.recordings import RecordingCatalog

TERMINAL = {"stopped", "completed", "failed", "interrupted", "abstained", "failed_task", "complete"}


class KioskBoundary:
    """Plain ASGI boundary: preserve streaming/disconnect receive semantics."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            if not (
                path in {"/", "/api/health"}
                or path.startswith("/assets/")
                or path.startswith("/api/playground/")
            ):
                response = JSONResponse(
                    status_code=404, content={"detail": "Not available in exhibition mode"}
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class _Stopped(Exception):
    pass


class _Session:
    def __init__(self, manager, identifier, request):
        self.manager, self.id, self.request = manager, identifier, request
        self.status, self.revision, self.rounds = "preparing", 0, 0
        self.auto, self.permit, self.stop_requested = False, True, False
        self.error = None
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.task = None

    async def status_record(self):
        await self.manager.store.call(
            "set_status", self.id, self.status, {"rounds": self.rounds, "error": self.error}
        )

    async def admit(self):
        while True:
            async with self.lock:
                if self.stop_requested:
                    raise _Stopped()
                if self.auto or self.permit:
                    self.permit = False
                    self.status = "running"
                    await self.status_record()
                    return
                self.status = "paused"
                await self.status_record()
                self.wake.clear()
            await self.wake.wait()

    async def observer(self, run_id, belief):
        self.rounds += 1
        await self.manager.store.call(
            "append", self.id, "playground_round", {"run": run_id, "round": self.rounds}
        )
        result = (await self.manager.store.call("get_run", run_id))["result"]
        if (
            self.rounds < ROUNDS
            and result["steps"]
            and not result["unsafe"]
            and not result["success"]
        ):
            await self.admit()

    async def drive(self):
        cpu, wall = time.process_time(), time.perf_counter()
        try:
            store, artifacts = self.manager.store, self.manager.artifacts
            module_options = (
                {"reasoner": self.manager.reasoner} if self.request.pipeline == "local_llm" else {}
            )
            world, learning, training = await prepare_queue(
                store,
                artifacts,
                self.request.scenario,
                self.request.seed,
                self.id,
                self.manager.policy,
                **module_options,
            )
            await store.call(
                "append",
                self.id,
                "playground_ready",
                {
                    "learning": learning.journal_run,
                    "lifecycle": learning.backend.lifecycle.journal_run,
                    "training": training,
                    "initial": (await world.observe()).model_dump(),
                    "preparation_cpu_seconds": time.process_time() - cpu,
                    "preparation_wall_seconds": time.perf_counter() - wall,
                    "model_version": learning.backend.lifecycle.epoch.model_version,
                    "pipeline": self.request.pipeline,
                },
            )
            await self.admit()
            runner = cognitive_runner(
                store, artifacts, world, learning, self.id, self.observer, self.manager.policy
            )
            result = await runner.run(world, ROUNDS)
            self.status = (
                "stopped"
                if self.stop_requested
                else "completed"
                if self.rounds == ROUNDS and result.status == "round_limit"
                else result.status
            )
            await store.call("append", self.id, "playground_result", result.model_dump())
        except _Stopped:
            self.status = "stopped"
        except asyncio.CancelledError:
            self.status = "interrupted"
            self.error = "Service stopped; inspect receipts before starting a new session"
        except Exception as error:
            self.status, self.error = "failed", type(error).__name__
        finally:
            self.auto = False
            await self.manager.store.call(
                "append",
                self.id,
                "playground_closed",
                {
                    "status": self.status,
                    "error": self.error,
                    "cpu_seconds": time.process_time() - cpu,
                    "wall_seconds": time.perf_counter() - wall,
                    "external_api_cost_usd": 0,
                    "compute_dollar_cost": None,
                },
            )
            await self.status_record()

    async def command(self, request):
        async with self.lock:
            if request.revision != self.revision:
                raise HTTPException(409, "Control revision changed; refresh the session")
            if self.status in TERMINAL:
                raise HTTPException(409, "Session has ended; create a new session")
            if request.command == "step" and self.status != "paused":
                raise HTTPException(409, "Next Step requires a settled pause")
            # Persist the operator command before granting its process-local permit.
            await self.manager.store.call(
                "append",
                self.id,
                "playground_control",
                {
                    "revision": self.revision + 1,
                    "command": request.command,
                },
            )
            self.revision += 1
            self.auto = request.command == "auto"
            self.permit = request.command == "step"
            if request.command == "stop":
                self.stop_requested, self.status = True, "stopping"
            elif request.command == "pause":
                self.status = "paused" if self.status == "paused" else "pausing"
            elif self.status != "preparing":
                self.status = "running"
            self.wake.set()
            await self.status_record()


class PlaygroundManager:
    def __init__(self, store, artifacts, root, recordings, policy=None, reasoner=None):
        self.store, self.artifacts, self.root = store, artifacts, Path(root)
        self.catalog = RecordingCatalog(recordings)
        self.active = {}
        self.admission = asyncio.Lock()
        self.closing = False
        self.policy = (policy or Policy()).model_copy(deep=True)
        self.reasoner, self.owns_reasoner = reasoner, False
        self.reasoner_error = None

    async def connect_modules(self):
        if self.reasoner is not None or os.getenv("PREACT_PLAYGROUND_LOCAL_LLM") != "1":
            return
        from preact.engines.llamacpp import LocalNemotron

        try:
            self.reasoner = await LocalNemotron.connect()
            self.owns_reasoner = True
        except Exception as error:
            # Keep native/offline Replay available; never substitute a fake LLM.
            self.reasoner_error = type(error).__name__

    def configurations(self):
        return [
            {"id": "native", "label": "数理モデル + 数値シミュレータ", "available": True},
            {
                "id": "local_llm",
                "label": "LLMの行動提案 + 数理モデル + 数値シミュレータ",
                "available": self.reasoner is not None,
                "engine_id": self.reasoner.capabilities.engine_id if self.reasoner else None,
                "engine_version": self.reasoner.capabilities.version if self.reasoner else None,
                "status": "connected" if self.reasoner else "not_connected",
                "error_type": self.reasoner_error,
            },
        ]

    async def recover(self):
        for identifier in await self.identifiers():
            record = await self.store.call("get_run", identifier)
            if record and record["status"] not in TERMINAL:
                await self.store.call(
                    "set_status",
                    identifier,
                    "interrupted",
                    {
                        "reason": "Restart; historical view only; never redispatch",
                    },
                )

    async def identifiers(self):
        if not self.root.exists():
            return []
        return [
            p.stem
            for p in sorted(
                self.root.glob("*.session"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            if len(p.stem) == 32 and all(c in "0123456789abcdef" for c in p.stem)
        ]

    async def start(self, request):
        async with self.admission:
            if self.closing:
                raise HTTPException(503, "Service is shutting down")
            if request.pipeline == "local_llm" and self.reasoner is None:
                raise HTTPException(503, "Local LLM module is not connected")
            if any(s.status not in TERMINAL for s in self.active.values()):
                raise HTTPException(409, "Finish the current Queue session first")
            identifier = uid()
            await self.store.call(
                "create_run",
                {
                    "kind": "playground_session",
                    "request": request.model_dump(),
                },
                run_id=identifier,
            )
            await self.store.call("set_status", identifier, "preparing")

            def register():
                self.root.mkdir(parents=True, exist_ok=True)
                target = self.root / f"{identifier}.session"
                temp = target.with_suffix(".tmp")
                temp.write_text(identifier)
                os.replace(temp, target)

            await durable_io(register)
            session = _Session(self, identifier, request)
            self.active[identifier] = session
            session.task = asyncio.create_task(session.drive())
            return identifier

    async def close(self):
        self.closing = True
        async with self.admission:
            tasks = [s.task for s in self.active.values() if s.task and not s.task.done()]
            # Process shutdown retains normal Runtime interruption/receipt semantics.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.owns_reasoner:
                await self.reasoner.aclose()

    async def snapshot(self, identifier, after=0, reference=None, _attempt=0):
        recorded = self.catalog.get(identifier)
        if recorded:
            events, summary, details = recorded.events, recorded.session, recorded.artifacts
        else:
            if identifier not in await self.identifiers():
                raise HTTPException(404, "Playground session not found")
            head = await self.store.call("run_heads", [identifier])
            record = await self.store.call("get_run", identifier)
            if not record or record["config"].get("kind") != "playground_session":
                raise HTTPException(404, "Playground session not found")
            request = PlaygroundStart.model_validate(record["config"]["request"])
            controls = await self.store.call("read_events", identifier)
            sources, ready = {}, None
            rounds, revision = 0, 0
            for event in controls:
                data = event["data"]
                if event["kind"] == "playground_ready":
                    ready = data
                    sources.update({data["learning"]: "learning", data["lifecycle"]: "lifecycle"})
                elif event["kind"] == "playground_round":
                    rounds = data["round"]
                elif event["kind"] == "playground_control":
                    revision = data["revision"]
            if ready:
                journal = await self.store.call("read_events", ready["learning"])
                sources.update(
                    {
                        e["data"]["run"]: "world"
                        for e in journal
                        if e["kind"] == "learning_round_open"
                    }
                )
            events = await read_sources(self.store, sources)
            details = await durable_io(artifact_details, self.artifacts, events)
            if await self.store.call("run_heads", [identifier]) != head:
                if _attempt >= 4:
                    raise HTTPException(503, "Session changed during retrieval; retry")
                return await self.snapshot(identifier, after, reference, _attempt + 1)
            active = self.active.get(identifier)
            provenance = {
                "scope": "CPU Queue simulation; one work unit per token; continuous Goal999 fixture",
                "scenario_changes": "Configured at Start only; no live World editing",
                "promotion": "Explicit only; unavailable to public controls",
                "order": "Presentation order by recorded timestamp; original per-run sequence retained",
                "pipeline": request.pipeline,
            }
            if ready:
                provenance["preparation"] = ready
            summary = PlaygroundSession(
                id=identifier,
                label=PRESETS[request.scenario][2],
                mode="live" if active and record["status"] not in TERMINAL else "recorded",
                scenario=request.scenario,
                status=record["status"],
                revision=revision,
                rounds=rounds,
                auto=active.auto if active else False,
                event_count=len(events),
                error=active.error if active else record["result"].get("error"),
                provenance=provenance,
            )
        reset = after > len(events) or bool(after and reference != events[after - 1].reference)
        return PlaygroundSnapshot(
            session=summary,
            events=events if reset else events[after:],
            artifacts=details,
            reset=reset,
        )

    async def sessions(self):
        rows = [r.session for r in self.catalog.records.values()]
        # Bound library work during long exhibitions; older sessions remain addressable by ID.
        for identifier in (await self.identifiers())[:8]:
            snapshot = await self.snapshot(identifier)
            rows.append(snapshot.session)
        return rows


def playground_router(manager):
    router = APIRouter(prefix="/api/playground")

    @router.get("/catalog")
    async def catalog():
        return {
            "sessions": await manager.sessions(),
            "replay_errors": manager.catalog.errors,
            "environment_changes": "start_only",
            "public_promotion": False,
            "module_configurations": manager.configurations(),
        }

    @router.post("/sessions", status_code=202)
    async def start(request: PlaygroundStart):
        return {"id": await manager.start(request)}

    @router.get("/sessions/{identifier}", response_model=PlaygroundSnapshot)
    async def snapshot(identifier: str, after: int = Query(0, ge=0), reference: str | None = None):
        return await manager.snapshot(identifier, after, reference)

    @router.post("/sessions/{identifier}/control", response_model=PlaygroundSnapshot)
    async def command(identifier: str, request: PlaygroundCommand):
        session = manager.active.get(identifier)
        if session is None:
            raise HTTPException(409, "Recorded Replay has no execution controls")
        await session.command(request)
        return await manager.snapshot(identifier)

    return router
