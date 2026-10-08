"""Wire compatibility fixtures; no live service validation."""

import json

import httpx
import pytest

from preact.core.evidence import bind_claims
from preact.core.interfaces import EngineFailure
from preact.core.models import PredictionRequest
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier
from preact.engines.remote import RemoteEngine


async def test_legacy_worker_receives_only_the_original_wire_fields():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    local = LocalVerifier(world)
    request = PredictionRequest(
        state=state, actions=[action], claims=bind_claims(world.task, state.id, [action])
    )
    prediction = await local.predict(request)
    posted = []

    def handle(req):
        posted.append(json.loads(req.content))
        return httpx.Response(200, json={"prediction": prediction.model_dump()})

    cap = local.capabilities.model_copy(update={"claim_contract_version": None})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", cap, client)
        await engine.predict(request)
    assert set(posted[0]) == {
        "schema_version",
        "state",
        "actions",
        "seed",
        "horizon",
        "deadline_seconds",
        "sample_budget",
    }


async def test_new_conditioning_never_silently_dispatches_to_legacy_worker():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    local = LocalVerifier(world)
    request = PredictionRequest(state=state, actions=[action], horizon=3)

    def unexpected(req):
        pytest.fail("Unsupported request reached worker")

    cap = local.capabilities.model_copy(update={"claim_contract_version": None})
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        engine = RemoteEngine("http://fixture", "fixture", cap, client)
        with pytest.raises(EngineFailure, match="contract"):
            await engine.predict(request)
