"""SDK-interface fixture backed by actual trusted probes; not live ConTree validation."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from preact.core.evidence import task_definitions
from preact.core.interfaces import EngineFailure
from preact.core.models import Capabilities, PredictionRequest
from preact.core.registry import Registry
from preact.domains.software import BASE, SAFE, SoftwareWorld, probe
from preact.engines.sandbox import Sandbox


class ImageFixture:
    def __init__(self, calls, source=BASE):
        self.uuid, self.source, self.calls = uuid4(), source, calls
        self.exit_code = 0
        self.result = SimpleNamespace(truncated=False, cost=0)

    async def run(self, **kwargs):
        assert kwargs["disposable"] is False and kwargs["preserve_env"] is False
        self.calls.append((str(self.uuid), kwargs))
        source = kwargs["files"]["/checkout.py"].decode()
        child = ImageFixture(self.calls, source)
        if "/probe.py" in kwargs["files"]:
            child.stdout = json.dumps(await probe(source))
        return child


def sandbox_fixture(world):
    calls, base = [], None
    base = ImageFixture(calls)

    async def use(name):
        return base

    engine = Sandbox.__new__(Sandbox)
    engine.world, engine.image_name = world, "unit-fixture-only"
    engine.sdk = SimpleNamespace(images=SimpleNamespace(use=use))
    engine.checkpoints = {}
    engine.capabilities = Capabilities(
        engine_id="token-factory-sandbox",
        version="contree-sdk-0.3.6",
        family="sandbox-execution",
        domains=["software"],
        evidence="executable",
        roles=["verifier"],
        supported_claims=task_definitions(world.task),
        verification_checks=world.task.required_checks,
        claim_contract_version="1",
        tier=2,
        max_samples=2,
        produces_successor=True,
        applicability="SDK conformance fixture only",
    )
    return engine, calls, base


async def test_sibling_futures_use_same_parent_checkpoint_and_do_not_mutate_authority():
    world = SoftwareWorld()
    engine, calls, base = sandbox_fixture(world)
    state = await world.observe()
    a, b = await world.propose(state, 2)
    registry = Registry([engine])
    unsafe, _ = await registry.predict(
        engine, PredictionRequest(state=state, actions=[a], sample_budget=2)
    )
    safe, _ = await registry.predict(
        engine, PredictionRequest(state=state, actions=[b], sample_budget=2)
    )
    assert unsafe.violations and not safe.violations
    assert unsafe.raw["parent_checkpoint"] == safe.raw["parent_checkpoint"]
    assert unsafe.sample_count == 2 and safe.sample_count == 1
    assert base.source == BASE and (await world.observe()).id == state.id
    assert calls[1][0] == calls[2][0]
    assert calls[1][1]["files"]["/probe.py"] != calls[2][1]["files"]["/probe.py"]


async def test_insufficient_checkpoint_budget_never_starts_cloud_work():
    engine, calls, _ = sandbox_fixture(SoftwareWorld())
    with pytest.raises(EngineFailure, match="two operation"):
        await engine.measure(SAFE, sample_budget=1)
    assert calls == []
