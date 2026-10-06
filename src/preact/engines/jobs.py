"""Nebius Serverless Jobs REST adapter, matching official ai/v1 documentation."""

import asyncio
import base64
import json
import math
import os

import httpx
from pydantic import Field

from preact.core.models import Contract, identity


class BatchSpec(Contract):
    image: str
    platform: str
    preset: str
    subnet_id: str
    output_volume: str
    timeout_seconds: int = Field(default=3600, ge=3600, le=604800)


class NebiusJobs:
    def __init__(self, token=None, project=None, client=None):
        self.token = token or os.environ["NEBIUS_ACCESS_TOKEN"]
        self.project = project or os.environ["NEBIUS_PROJECT_ID"]
        self.client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self.base = "https://api.nebius.cloud/ai/v1/jobs"

    async def aclose(self):
        if self._owns_client:
            await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    def payload(self, request: dict, spec: BatchSpec):
        encoded = json.dumps(request, sort_keys=True).encode()
        if len(encoded) > 65536:
            raise ValueError("Injected requests are limited to 64 KiB; use artifact references")
        digest = identity(request)
        return {
            "metadata": {"parentId": self.project, "name": "preact-" + digest[:20]},
            "spec": {
                "image": spec.image,
                "containerCommand": "python",
                "args": f"-m workers.batch /request.json /output/{digest}.json",
                "workingDir": "/app",
                "platform": spec.platform,
                "preset": spec.preset,
                "subnetId": spec.subnet_id,
                "timeout": f"{spec.timeout_seconds}s",
                "disk": {"type": "NETWORK_SSD", "sizeBytes": "268435456000"},
                "volumes": [
                    {"source": spec.output_volume, "containerPath": "/output", "mode": "READ_WRITE"}
                ],
                "injectedFiles": [
                    {
                        "containerPath": "/request.json",
                        "content": base64.b64encode(encoded).decode(),
                    }
                ],
            },
        }

    async def submit(self, request: dict, spec: BatchSpec):
        # Never retry POST blindly: an interrupted response can leave an allocated job.
        response = await self.client.post(
            self.base,
            headers={"Authorization": "Bearer " + self.token},
            json=self.payload(request, spec),
            timeout=30,
        )
        response.raise_for_status()
        return {
            "job_id": response.json()["resourceId"],
            "request_hash": identity(request),
            "result_key": identity(request) + ".json",
            "platform": "nebius-serverless-jobs",
        }

    async def get(self, job_id):
        response = await self.client.get(
            self.base + "/" + job_id, headers={"Authorization": "Bearer " + self.token}, timeout=15
        )
        response.raise_for_status()
        return response.json()

    async def cancel(self, job_id):
        response = await self.client.post(
            self.base + "/cancel",
            json={"id": job_id},
            headers={"Authorization": "Bearer " + self.token},
            timeout=15,
        )
        response.raise_for_status()

    async def wait(self, job_id, seconds=600):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Job wait budget must be finite and positive")
        deadline = asyncio.timeout(seconds)
        try:
            async with deadline:
                while True:
                    record = await self.get(job_id)
                    status = record["status"]["state"]
                    if status in {"COMPLETED", "FAILED", "CANCELED", "CANCELLED"}:
                        return record
                    await asyncio.sleep(1)
        except (Exception, asyncio.CancelledError) as error:
            # Once a job ID is known, abandoning a poll must request cancellation.
            # Drain that bounded request even if the caller cancels repeatedly.
            cleanup = asyncio.create_task(self._cancel_after_wait(job_id))
            cancelled_again = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled_again = True
            evidence = (
                {"job_id": job_id, "request_accepted": False, "error": "CancelledError"}
                if cleanup.cancelled()
                else cleanup.result()
            )
            evidence["terminal_state_confirmed"] = False
            if cancelled_again and not isinstance(error, asyncio.CancelledError):
                error = asyncio.CancelledError()
            elif deadline.expired() and isinstance(error, TimeoutError):
                error = TimeoutError("Serverless Job wait deadline exceeded")
            # Non-secret evidence remains inspectable even when cleanup failed.
            # An accepted cancel request does not prove the remote job has stopped.
            error.preact_cleanup = evidence
            raise error from None

    async def _cancel_after_wait(self, job_id):
        try:
            async with asyncio.timeout(5):
                await self.cancel(job_id)
            return {"job_id": job_id, "request_accepted": True}
        except Exception as error:
            evidence = {"job_id": job_id, "request_accepted": False, "error": type(error).__name__}
            if isinstance(error, httpx.HTTPStatusError):
                evidence["http_status"] = error.response.status_code
            return evidence
