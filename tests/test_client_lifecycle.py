import httpx
import pytest

from preact.core.interfaces import EngineFailure
from preact.core.models import Capabilities
from preact.engines.nebius import Nemotron
from preact.engines.remote import RemoteEngine
from preact.service.app import component_scope


async def test_partial_cloud_setup_failure_closes_already_created_model_client(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "fixture-key")
    monkeypatch.setenv("NEBIUS_MODEL", "fixture-model")
    monkeypatch.delenv("NEBIUS_STRONG_MODEL", raising=False)
    monkeypatch.delenv("CONTREE_API_KEY", raising=False)
    monkeypatch.delenv("CONTREE_IMAGE", raising=False)
    created = []
    original = Nemotron.__init__

    def capture(self, *args, **kwargs):
        original(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(Nemotron, "__init__", capture)
    with pytest.raises(EngineFailure, match="CONTREE_API_KEY"):
        async with component_scope("software", 0, "cloud"):
            pytest.fail("Missing sandbox admission must not yield working cloud components")
    assert len(created) == 1 and created[0].client.is_closed


async def test_failed_remote_discovery_closes_owned_client(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403)))
    monkeypatch.setattr("preact.engines.remote.httpx.AsyncClient", lambda: client)
    with pytest.raises(httpx.HTTPStatusError):
        await RemoteEngine.connect("http://fixture", "fixture-key")
    assert client.is_closed


async def test_engines_leave_borrowed_clients_open():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    caps = Capabilities(
        engine_id="fixture",
        version="1",
        family="fixture",
        domains=["software"],
        evidence="inference",
        tier=0,
        applicability="Fixture only",
    )
    remote = RemoteEngine("http://fixture", "fixture-key", caps, client)
    model = Nemotron("fixture", key="fixture-key", client=client)
    await remote.aclose()
    await model.aclose()
    assert not client.is_closed
    await client.aclose()
