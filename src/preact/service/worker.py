"""Durable leased prediction jobs, shared by isolated engine workers."""

import asyncio
import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from preact.core.interfaces import cleanup_evidence
from preact.core.models import PredictionRequest, uid
from preact.core.registry import Registry
from preact.core.store import Store


def worker_app(engine, store: Store, token: str):
    if not token:
        raise ValueError("A WORKER_TOKEN is required")
    owner = uid()
    registry = Registry([engine])
    closing = False

    async def settle(job, operation):
        try:
            result, _ = operation.result()
        except asyncio.CancelledError as error:
            await store.call("cancel_job", job["id"])
            await store.call("finish_cancelled_job", job["id"], owner, cleanup_evidence(error))
        except Exception as error:
            cleanup = cleanup_evidence(error)
            changed = await store.call(
                "finish_job", job["id"], owner, None, type(error).__name__, cleanup
            )
            if not changed:
                await store.call("finish_cancelled_job", job["id"], owner, cleanup)
        else:
            changed = await store.call("finish_job", job["id"], owner, result.model_dump())
            if not changed:
                # Completion raced a cancellation. Publication is suppressed;
                # the completed contract still witnesses the operation's end.
                await store.call(
                    "finish_cancelled_job",
                    job["id"],
                    owner,
                    {"request_accepted": False, "terminal_state_confirmed": True},
                )

    async def consume():
        while True:
            try:
                await consume_one()
            except SQLAlchemyError:
                # A failed/uncertain lease or result commit remains durable state.
                # Let its lease expire; do not publish invented successful evidence.
                await asyncio.sleep(0.25)

    async def consume_one():
        job = await store.call("lease", owner, 300)
        if not job:
            await asyncio.sleep(0.1)
            return
        operation = None
        attempted_settle = False
        try:
            request = PredictionRequest.model_validate(job["payload"]["request"])
            operation = asyncio.create_task(registry.predict(engine, request))
            while not operation.done():
                await asyncio.wait({operation}, timeout=0.1)
                if (await store.call("job", job["id"]))["status"] == "cancelling":
                    operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)
                    break
            attempted_settle = True
            await settle(job, operation)
        except (asyncio.CancelledError, SQLAlchemyError):
            raise
        except Exception as error:
            await store.call("finish_job", job["id"], owner, None, type(error).__name__)
        finally:
            if operation and not attempted_settle:
                if not operation.done():
                    operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)
                await settle(job, operation)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal closing
        closing = False
        consumer = asyncio.create_task(consume())
        app.state.consumer = consumer
        try:
            yield
        finally:
            closing = True
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)

    async def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Worker authorization required")

    app = FastAPI(lifespan=lifespan, dependencies=[Depends(authorize)])

    @app.exception_handler(SQLAlchemyError)
    async def unavailable_storage(request, error):
        return JSONResponse(status_code=503, content={"detail": "Durable storage is unavailable"})

    @app.get("/health")
    async def health():
        await store.call("healthy")
        if app.state.consumer.done():
            raise HTTPException(503, "Prediction consumer is unavailable")
        return {"status": "ok"}

    @app.get("/capabilities")
    async def capabilities():
        return engine.capabilities.model_dump()

    @app.post("/predictions", status_code=202)
    async def predict(request: PredictionRequest):
        if closing:
            raise HTTPException(503, "Worker is shutting down")
        if request.deadline_seconds > 240:
            raise HTTPException(422, "Worker deadline exceeds lease limit")
        return {"id": await store.call("enqueue", request.model_dump()), "status": "queued"}

    @app.get("/jobs/{job_id}")
    async def poll(job_id: str):
        job = await store.call("job", job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return {
            "id": job_id,
            "job_protocol_version": "2",
            "status": job["status"],
            "error": job["error"],
            "prediction": job["payload"].get("prediction"),
            "operation_finished": job["payload"].get("operation_finished") is True
            or (job["status"] == "cancelled" and not job["owner"]),
            "terminal_state_confirmed": job["payload"].get("terminal_state_confirmed") is True
            or (job["status"] == "cancelled" and not job["owner"]),
            "cleanup": job["payload"].get("cleanup"),
        }

    @app.delete("/jobs/{job_id}")
    async def cancel(job_id: str):
        await store.call("cancel_job", job_id)
        return {"id": job_id, "status": "cancellation_requested"}

    return app
