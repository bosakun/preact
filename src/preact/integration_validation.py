"""Live Token Factory acceptance probe; missing access is a durable failed gate."""

import os
import time
from datetime import datetime, timezone

import httpx

from preact.core.models import PredictionRequest, identity
from preact.core.registry import Registry
from preact.domains.physical import PhysicalWorld
from preact.domains.software import SoftwareWorld
from preact.engines.nebius import Nemotron


async def validate_token_factory(model, key, client=None, catalog_only=False):
    report = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": "nebius-token-factory",
        "status": "blocked",
        "catalog": None,
        "inference": [],
        "scope": "Model discovery and real structured prediction requests; no Sandbox/GPU/deployment certification",
    }
    if not key:
        report["blocker"] = "NEBIUS_API_KEY is not configured"
        return report
    owns_client = client is None
    client = client or httpx.AsyncClient()
    # Catalog discovery is useful before a model has been selected; no default ID.
    base = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
    try:
        response = await client.get(
            base + "/models", headers={"Authorization": "Bearer " + key}, timeout=15
        )
        response.raise_for_status()
        body = response.json()
        identifiers = sorted(
            {entry["id"] for entry in body["data"] if isinstance(entry.get("id"), str)}
        )
        report["catalog"] = {
            "model_ids": identifiers,
            "response_hash": identity(body),
            "http_status": response.status_code,
            "request_id": response.headers.get("x-request-id"),
        }
        if catalog_only:
            report["status"] = "catalog_verified"
            return report
        if not model:
            report["blocker"] = (
                "NEBIUS_MODEL is not configured; select an exact admitted catalog ID"
            )
            return report
        if model not in identifiers:
            report["blocker"] = "Configured NEBIUS_MODEL is absent from the admitted model catalog"
            return report
        engine = Nemotron(model, key=key, client=client)
        registry = Registry([engine])
        for world in (SoftwareWorld(), PhysicalWorld()):
            state = await world.observe()
            actions = await world.propose(state, 2)
            request = PredictionRequest(state=state, actions=[actions[-1]])
            started = time.monotonic()
            prediction, cached = await registry.predict(engine, request)
            report["inference"].append(
                {
                    "domain": world.task.domain,
                    "request_hash": identity(request.model_dump()),
                    "state_id": state.id,
                    "action_fingerprint": actions[-1].fingerprint,
                    "prediction": prediction.model_dump(),
                    "cached": cached,
                    "latency_ms": (time.monotonic() - started) * 1000,
                }
            )
        report["status"] = "inference_verified"
    except Exception as error:
        report["status"] = "failed"
        # Do not serialize exception text, URLs, headers or raw error responses.
        report["failure"] = {"type": type(error).__name__}
        if isinstance(error, httpx.HTTPStatusError):
            report["failure"]["http_status"] = error.response.status_code
    finally:
        if owns_client:
            await client.aclose()
    return report
