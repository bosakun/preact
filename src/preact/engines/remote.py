"""Versioned asynchronous worker interoperability for Isaac/Cosmos jobs."""

import asyncio
import re

import httpx

from preact.core.evidence import metric_definition
from preact.core.interfaces import CleanupEvidence, EngineFailure
from preact.core.models import Capabilities, Prediction


class RemoteEngine:
    def __init__(self, endpoint, token, capabilities, client=None):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.capabilities = capabilities
        self.client = client or httpx.AsyncClient()
        self._owns_client = client is None

    @classmethod
    async def connect(cls, endpoint, token, client=None):
        owned = client is None
        client = client or httpx.AsyncClient()
        try:
            response = await client.get(
                endpoint.rstrip("/") + "/capabilities",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            response.raise_for_status()
            engine = cls(endpoint, token, Capabilities.model_validate(response.json()), client)
            engine._owns_client = owned
            return engine
        except BaseException:
            if owned:
                await client.aclose()
            raise

    async def aclose(self):
        if self._owns_client:
            await self.client.aclose()

    async def predict(self, request):
        wire = request.model_dump()
        if self.capabilities.claim_contract_version is None:
            legacy_metrics = [
                metric_definition("action_postconditions/v1", "success"),
                metric_definition("constraint_violation/v1", "risk"),
            ]
            if (
                request.conditioning is not None
                or request.horizon != 1
                or any(
                    c.horizon != 1
                    or c.conditions
                    or c.definition.scope != "root_action"
                    or not (
                        c.definition in legacy_metrics
                        or (
                            c.definition.namespace.startswith("legacy-check:")
                            and c.definition.version == "legacy"
                        )
                    )
                    for c in request.claims
                )
            ):
                raise EngineFailure("Worker does not advertise claim/conditioning contract support")
            wire = request.model_dump(exclude={"claims", "conditioning"})
        headers = {"Authorization": f"Bearer {self.token}"}
        job_id = None
        terminal = None
        termination_confirmed = False
        worker_cleanup = None
        try:
            # One deadline covers admission as well as polling. An interrupted
            # POST may have allocated work: never repeat submission blindly.
            async with asyncio.timeout(request.deadline_seconds):
                response = await self.client.post(
                    self.endpoint + "/predictions",
                    headers=headers,
                    json=wire,
                    timeout=request.deadline_seconds,
                )
                response.raise_for_status()
                job = response.json()
                if "prediction" in job:
                    return self._prediction(job["prediction"])
                identifier = job["id"]
                if not isinstance(identifier, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,128}", identifier
                ):
                    raise EngineFailure("Worker returned an invalid job identifier")
                job_id = identifier
                while True:
                    response = await self.client.get(
                        self.endpoint + f"/jobs/{job_id}",
                        headers=headers,
                        timeout=min(10, request.deadline_seconds),
                    )
                    response.raise_for_status()
                    body = response.json()
                    if body["status"] == "complete":
                        terminal = "complete"
                        termination_confirmed = body.get("terminal_state_confirmed") is True
                        prediction = self._prediction(body["prediction"])
                        prediction.raw = {
                            **prediction.raw,
                            "worker_job_id": job_id,
                            "worker_status": terminal,
                        }
                        return prediction
                    if body["status"] in {"failed", "cancelled", "interrupted"}:
                        terminal = body["status"]
                        if body.get("cleanup") is not None:
                            worker_cleanup = CleanupEvidence.model_validate(body["cleanup"])
                        termination_confirmed = body.get("terminal_state_confirmed") is True and (
                            worker_cleanup is None or worker_cleanup.terminal_state_confirmed
                        )
                        raise EngineFailure(f"Worker job {terminal}")
                    if body["status"] not in {"queued", "running", "cancelling"}:
                        raise EngineFailure("Worker returned an invalid job status")
                    await asyncio.sleep(0.25)
        except (Exception, asyncio.CancelledError) as error:
            evidence = {
                "job_id": job_id,
                "operation_kind": "remote_job",
                "submission_outcome": "acknowledged" if job_id else "unconfirmed",
                "request_accepted": False,
                "terminal_state_confirmed": termination_confirmed,
                "worker_status": terminal,
            }
            if worker_cleanup is not None:
                for key in ("error", "http_status"):
                    value = getattr(worker_cleanup, key)
                    if value is not None:
                        evidence[key] = value
            if job_id and terminal is None:
                cleanup = asyncio.create_task(self._cancel_job(job_id, headers))
                cancelled_again = False
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled_again = True
                evidence.update(cleanup.result())
                if cancelled_again and not isinstance(error, asyncio.CancelledError):
                    error = asyncio.CancelledError()
            error.preact_cleanup = evidence
            raise error from None

    def _prediction(self, payload):
        result = Prediction.model_validate(payload)
        if self.capabilities.claim_contract_version is None:
            # Host Registry supplies the negotiated legacy request scope.
            result.requested_claims = None
        return result

    async def _cancel_job(self, job_id, headers):
        # Acknowledgment is not terminal-state confirmation. Drain through
        # repeated caller cancellation; the independent bound limits cleanup.
        try:
            async with asyncio.timeout(5):
                response = await self.client.delete(
                    self.endpoint + f"/jobs/{job_id}", headers=headers, timeout=5
                )
                response.raise_for_status()
            return {"request_accepted": True}
        except Exception as error:
            evidence = {"request_accepted": False, "error": type(error).__name__}
            if isinstance(error, httpx.HTTPStatusError):
                evidence["http_status"] = error.response.status_code
            return evidence
