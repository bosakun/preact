import json

import httpx
import pytest

from preact.core.models import PredictionRequest
from preact.domains.cloud import validate_generated_patch
from preact.domains.software import SAFE, SoftwareWorld
from preact.engines.nebius import Nemotron


def test_generated_patch_rejects_side_effects_and_local_runner_remains_restricted():
    validate_generated_patch(SAFE)
    for source in [
        "import os\ndef checkout(a,b): return os.getenv('KEY')",
        "def checkout(a,b): return print('fake tests')",
        "def checkout(a,b): return a.__class__",
        "exec('malicious')\ndef checkout(a,b): return 0",
    ]:
        with pytest.raises(ValueError):
            validate_generated_patch(source)


async def test_nebius_wire_contract_does_not_promote_self_confidence(monkeypatch):
    # Mock transport checks the adapter, explicitly not real sponsor integration.
    monkeypatch.setenv("NEBIUS_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("NEBIUS_OUTPUT_USD_PER_MILLION", "2")

    def handle(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["model"] == "nvidia/unit-test-model"
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            json={
                "id": "fixture-request",
                "model": body["model"],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "success": {"value": 0.9, "measured": True},
                                    "risk": {"value": 0.01, "measured": True},
                                    "assumptions": ["Reasoning only"],
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    engine = Nemotron("nvidia/unit-test-model", key="fixture-token", client=client)
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    result = await engine.predict(PredictionRequest(state=state, actions=[action]))
    assert not result.success.measured and not result.risk.measured
    assert result.cost_usd == pytest.approx(0.0002)
    assert result.raw["request_id"] == "fixture-request"
    await client.aclose()
