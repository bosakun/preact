"""Injected HTTP completion evidence: no live provider/model validation."""

import copy
import json

import httpx
import pytest

from preact.core.interfaces import EngineFailure
from preact.core.models import PredictionRequest
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.engines.nebius import Nemotron


@pytest.fixture
def response():
    return {
        "id": "fixture-completion",
        "model": "fixture-model",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "success": {"value": 0.99, "measured": True},
                            "risk": {"value": 0.001, "measured": True},
                            "assumptions": ["Injected transport, not live inference"],
                        }
                    ),
                },
            }
        ],
    }


async def predict(response):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        engine = Nemotron("fixture-model", key="fixture-private-sentinel", client=client)
        world = SoftwareWorld()
        state = await world.observe()
        action = (await world.propose(state, 2))[1]
        return await engine.predict(PredictionRequest(state=state, actions=[action]))


@pytest.mark.parametrize(
    "fault",
    [
        "model",
        "length",
        "content_filter",
        "tool_calls",
        "missing-finish",
        "missing-id",
        "role",
        "multiple-choices",
        "bad-index",
        "invalid-schema",
        "secret-echo",
    ],
)
async def test_incomplete_or_mismatched_response_never_yields_prediction(response, fault):
    choice = response["choices"][0]
    if fault == "model":
        response["model"] = "different-model"
    elif fault in ("length", "content_filter", "tool_calls"):
        choice["finish_reason"] = fault
    elif fault == "missing-finish":
        del choice["finish_reason"]
    elif fault == "missing-id":
        del response["id"]
    elif fault == "role":
        choice["message"]["role"] = "user"
    elif fault == "multiple-choices":
        response["choices"].append(copy.deepcopy(choice))
    elif fault == "bad-index":
        choice["index"] = False
    elif fault == "invalid-schema":
        choice["message"]["content"] = '{"private-diagnostic-sentinel": true}'
    else:
        choice["message"]["content"] = '{"private":"fixture-private-sentinel"}'
    with pytest.raises(EngineFailure) as error:
        await predict(response)
    assert "private-diagnostic-sentinel" not in str(error.value)
    assert "fixture-private-sentinel" not in str(error.value)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": False, "completion_tokens": 50},
        {"prompt_tokens": -1, "completion_tokens": 50},
        {"prompt_tokens": "private-diagnostic", "completion_tokens": 50},
    ],
)
async def test_unavailable_or_malformed_usage_stays_unknown_without_copying_untrusted_values(
    response, monkeypatch, usage
):
    monkeypatch.setenv("NEBIUS_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("NEBIUS_OUTPUT_USD_PER_MILLION", "2")
    response["usage"] = usage
    result = await predict(response)
    assert not result.raw["cost_known"] and result.cost_usd == 0
    assert "private-diagnostic" not in json.dumps(result.raw)
    assert not result.success.measured and not result.risk.measured


@pytest.mark.parametrize(
    "rates,expected",
    [(("1", "2"), 0.0002), (("0", "2"), 0.0001), (("0", "0"), None), ((None, "2"), None)],
)
async def test_prices_and_usage_produce_one_consistent_cost(response, monkeypatch, rates, expected):
    for name, value in zip(
        ("NEBIUS_INPUT_USD_PER_MILLION", "NEBIUS_OUTPUT_USD_PER_MILLION"), rates, strict=True
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    result = await predict(response)
    assert result.raw["cost_known"] is (expected is not None)
    assert result.cost_usd == pytest.approx(expected or 0)
    assert result.raw["cost_usd"] == result.cost_usd


@pytest.mark.parametrize("rate", ["-1", "nan", "inf", "private-diagnostic-sentinel"])
def test_invalid_operator_pricing_fails_without_echoing_configuration(monkeypatch, rate):
    monkeypatch.setenv("NEBIUS_INPUT_USD_PER_MILLION", rate)
    monkeypatch.setenv("NEBIUS_OUTPUT_USD_PER_MILLION", "2")
    with pytest.raises(EngineFailure) as error:
        Nemotron("fixture-model", key="fixture-key")
    assert "private-diagnostic-sentinel" not in str(error.value)


async def test_mismatched_model_abstains_through_core_without_outcomes_or_error_labels(
    response, tmp_path
):
    response["model"] = "different-model"
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    world = SoftwareWorld()
    original = await world.observe()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        engine = Nemotron("fixture-model", key="fixture-key", client=client)
        runtime = Runtime(store, Artifacts(str(tmp_path / "artifacts")), Registry([engine]))
        result = await runtime.run(world)
    assert not result["success"] and result["status"] == "abstained" and result["steps"] == 0
    assert (await world.observe()).id == original.id
    assert not store.error_rows()
    events = store.read_events(runtime.run_id)
    assert any(e["kind"] == "engine_failed" for e in events)
    assert not any(e["kind"] in ("prediction", "execution_intent", "outcome") for e in events)


async def test_registered_pricing_does_not_change_during_an_inflight_call(response, monkeypatch):
    monkeypatch.setenv("NEBIUS_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("NEBIUS_OUTPUT_USD_PER_MILLION", "2")

    def handle(_):
        monkeypatch.setenv("NEBIUS_INPUT_USD_PER_MILLION", "999")
        monkeypatch.setenv("NEBIUS_OUTPUT_USD_PER_MILLION", "999")
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = Nemotron("fixture-model", key="fixture-key", client=client)
        _, provenance = await engine.json_call("fixture prompt", {}, 0, 10)
    assert provenance["cost_known"] and provenance["cost_usd"] == pytest.approx(0.0002)
