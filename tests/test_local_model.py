"""Injected local-model boundary tests. These do not claim actual model/GPU inference."""

import copy
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from preact.core.interfaces import EngineFailure
from preact.core.models import PredictionRequest
from preact.domains.software import SoftwareWorld
from preact.engines.llamacpp import LocalModelManifest, LocalNemotron, sha256_file
from preact.engines.reasoning import ModelProposer
from preact.service.app import component_scope, create_app


@pytest.fixture
def configuration(tmp_path):
    model = tmp_path / "model.gguf"
    runner = tmp_path / "llama-server"
    model.write_bytes(b"injected file identity only, not real weights")
    runner.write_bytes(b"injected runner identity only, not a binary")
    key = tmp_path / "private-token"
    key.write_text("fixture-private-token-never-publish-12345678")
    key.chmod(0o600)
    manifest = LocalModelManifest(
        model="nvidia/fixture",
        checkpoint_revision="a" * 40,
        original_weights_sha256="b" * 64,
        model_path=str(model),
        model_sha256=sha256_file(model),
        quantization="fixture",
        runner_dir=str(tmp_path),
        runner_revision="c" * 40,
        runner_build="fixture-build",
        runner_files={runner.name: sha256_file(runner)},
        hardware="injected fixture",
        acceleration="injected fixture",
        license_url="https://www.nvidia.com/fixture",
        license_sha256="d" * 64,
    )
    path = tmp_path / "manifest.json"
    path.write_text(manifest.model_dump_json())
    props = {
        "model_alias": manifest.model,
        "build_info": manifest.runner_build,
        "model_path": str(model),
        "model_ftype": "fixture",
        "total_slots": 1,
        "default_generation_settings": {"n_ctx": 8192},
        "chat_template": "fixture",
        "ui": False,
        "cors_proxy_enabled": False,
    }
    return path, key, manifest, props


def completion(manifest, payload=None):
    return {
        "model": manifest.model,
        "id": "injected-completion",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        payload
                        or {
                            "success": {"value": 0.99, "measured": True},
                            "risk": {"value": 0, "measured": True},
                            "assumptions": ["Injected transport, not actual inference"],
                        }
                    ),
                },
            }
        ],
    }


async def connect(configuration, handler):
    path, key, _, _ = configuration
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        engine = await LocalNemotron.connect(path, key, client=client)
        return engine, client
    except BaseException:
        await client.aclose()
        raise


async def test_model_cannot_promote_inference_to_measurement_or_cloud_evidence(configuration):
    _, key, manifest, props = configuration
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer " + key.read_text()
        return httpx.Response(200, json=props if request.method == "GET" else completion(manifest))

    engine, client = await connect(configuration, handler)
    try:
        world = SoftwareWorld()
        state = await world.observe()
        action = (await world.propose(state, 2))[1]
        result = await engine.predict(PredictionRequest(state=state, actions=[action]))
        assert not result.success.measured and not result.risk.measured
        assert result.engine_version == engine.capabilities.version != manifest.model
        assert result.raw["platform"] == "local-llama.cpp"
        assert not result.raw["cost_known"]
        assert not result.raw["live_nebius_validation"] and not result.raw["nvidia_gpu_validation"]
        assert key.read_text() not in result.model_dump_json()
        body = json.loads(next(r.content for r in requests if r.method == "POST"))
        assert body["response_format"]["json_schema"]["schema"]["type"] == "object"
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        await engine.aclose()
        assert not client.is_closed
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "field", ["model_alias", "build_info", "model_path", "ui", "cors_proxy_enabled"]
)
async def test_serving_mismatch_fails_before_inference(configuration, field):
    props = copy.deepcopy(configuration[3])
    props[field] = True if field in {"ui", "cors_proxy_enabled"} else "wrong"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=props)

    with pytest.raises(EngineFailure):
        await connect(configuration, handler)
    assert all(r.method == "GET" for r in requests)


@pytest.mark.parametrize("fault", ["weights", "runner", "permissions"])
async def test_file_or_token_identity_failure_never_contacts_runner(configuration, fault):
    path, key, manifest, _ = configuration
    if fault == "permissions":
        key.chmod(0o644)
    else:
        target = manifest.model_path if fault == "weights" else str(path.parent / "llama-server")
        from pathlib import Path

        Path(target).write_text("changed")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("No request allowed"))
    )
    try:
        with pytest.raises(EngineFailure):
            await LocalNemotron.connect(path, key, client=client)
    finally:
        await client.aclose()


async def test_serving_change_after_completion_discards_prediction(configuration):
    _, _, manifest, original = configuration
    changed = False

    def handler(request):
        nonlocal changed
        if request.method == "POST":
            changed = True
            return httpx.Response(200, json=completion(manifest))
        props = copy.deepcopy(original)
        if changed:
            props["default_generation_settings"]["n_ctx"] = 4096
        return httpx.Response(200, json=props)

    engine, client = await connect(configuration, handler)
    try:
        with pytest.raises(EngineFailure, match="identity"):
            await engine.json_call("fixture", {}, 0, 2)
    finally:
        await client.aclose()


@pytest.mark.parametrize("fault", ["model", "truncated", "echo", "oversized"])
async def test_invalid_completions_never_yield_structured_evidence(configuration, fault):
    _, key, manifest, props = configuration
    body = completion(manifest)
    if fault == "model":
        body["model"] = "other"
    elif fault == "truncated":
        body["choices"][0]["finish_reason"] = "length"
    elif fault == "echo":
        body["choices"][0]["message"]["content"] = json.dumps({"private": key.read_text()})
    else:
        body["choices"][0]["message"]["content"] = "x" * 140000
    engine, client = await connect(
        configuration, lambda r: httpx.Response(200, json=props if r.method == "GET" else body)
    )
    try:
        with pytest.raises(EngineFailure) as error:
            await engine.json_call("fixture", {}, 0, 2)
        assert key.read_text() not in str(error.value)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1",
        "http://example.com",
        "http://localhost",
        "http://127.0.0.1/proxy",
        "http://user:password@127.0.0.1",
    ],
)
def test_local_engine_refuses_remote_or_ambiguous_endpoint(configuration, url):
    with pytest.raises(EngineFailure):
        LocalNemotron(url, configuration[2], "fixture")


async def test_missing_local_model_never_becomes_heuristic_mode(monkeypatch):
    monkeypatch.delenv("PREACT_LOCAL_MODEL_MANIFEST", raising=False)
    monkeypatch.delenv("PREACT_LOCAL_MODEL_KEY_FILE", raising=False)
    with pytest.raises(EngineFailure):
        async with component_scope("software", 0, "local-model"):
            pytest.fail("Missing model must not provide a world")


async def test_health_mode_does_not_certify_configuration(tmp_path):
    app = create_app("sqlite:///:memory:", str(tmp_path), mode="local-model")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        health = (await client.get("/api/health")).json()
        assert health["mode"] == "local-model"
        assert not health["live_sponsor_validation"]


async def test_rank_schema_binds_real_indexes_and_removes_paired_prompt_noise():
    class Capture:
        def __init__(self):
            self.calls = []

        async def json_call(self, prompt, schema, seed, deadline):
            self.calls.append((json.loads(prompt), schema))
            return {"ranking": [1, 0]}, {"cost_known": False, "cost_usd": 0}

    world = SoftwareWorld()
    engine = Capture()
    proposer = ModelProposer(world, engine)
    first = await proposer.propose(await world.observe(), 2)
    second = await proposer.propose(await world.observe(), 2)
    assert first[0].name == second[0].name == "Prepare a reusable validator"
    assert first[0].id != second[0].id
    assert engine.calls[0] == engine.calls[1]
    schema = engine.calls[0][1]["properties"]["ranking"]
    assert schema["items"]["minimum"] == 0 and schema["items"]["maximum"] == 1


async def test_failed_benchmark_keeps_elapsed_work_and_unknown_cost(tmp_path, monkeypatch):
    import asyncio

    from preact.benchmarks import benchmark
    from preact.core.registry import Registry

    class FailureWorld(SoftwareWorld):
        async def propose(self, state, width):
            await asyncio.sleep(0.02)
            raise EngineFailure("injected agent failure")

    @asynccontextmanager
    async def fixture_components(*args):
        yield FailureWorld(), Registry([])

    monkeypatch.setattr("preact.benchmarks.component_scope", fixture_components)
    report = await benchmark(str(tmp_path), seeds=1)
    assert all(r["status"] == "failed" for r in report["episodes"])
    assert all(r["latency_ms"] >= 20 and not r["cost_known"] for r in report["episodes"])
    assert all(r["execution_attempts"] == 0 for r in report["episodes"])
