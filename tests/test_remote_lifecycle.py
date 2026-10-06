"""Transport fixtures validate cleanup, never claim actual NVIDIA/Nebius jobs."""

import asyncio
import json
import time

import httpx
import pytest

from preact.core.interfaces import EngineFailure, cleanup_evidence
from preact.core.models import Policy, PredictionRequest
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier
from preact.engines.remote import RemoteEngine


@pytest.fixture
async def inputs():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    return LocalVerifier(world), PredictionRequest(state=state, actions=[action])


@pytest.mark.parametrize("phase", ["submit", "poll"])
async def test_whole_request_deadline_and_unknown_submission_are_explicit(inputs, phase):
    verifier, request = inputs
    request.deadline_seconds = 0.02
    methods = []

    async def handle(req):
        methods.append(req.method)
        if req.method == "POST" and phase == "poll":
            return httpx.Response(202, json={"id": "fixture-job"})
        if req.method == "DELETE":
            return httpx.Response(200, json={"status": "cancellation_requested"})
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        clock = time.monotonic()
        with pytest.raises(TimeoutError) as failure:
            await engine.predict(request)
        assert time.monotonic() - clock < 0.2
        evidence = failure.value.preact_cleanup
        assert methods == (["POST"] if phase == "submit" else ["POST", "GET", "DELETE"])
        assert not evidence["terminal_state_confirmed"]
        assert evidence["submission_outcome"] == (
            "unconfirmed" if phase == "submit" else "acknowledged"
        )
        assert evidence["request_accepted"] == (phase == "poll")


async def test_repeated_cancellation_drains_remote_cleanup_before_client_can_close(inputs):
    verifier, request = inputs
    polling, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def handle(req):
        calls.append(req.method)
        if req.method == "POST":
            return httpx.Response(202, json={"id": "fixture-job"})
        if req.method == "GET":
            polling.set()
            await asyncio.Event().wait()
        cancelling.set()
        await release.wait()
        return httpx.Response(200, json={"status": "cancellation_requested"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        operation = asyncio.create_task(engine.predict(request))
        try:
            await asyncio.wait_for(polling.wait(), 1)
            operation.cancel()
            await asyncio.wait_for(cancelling.wait(), 1)
            operation.cancel()
            await asyncio.sleep(0.01)
            assert not operation.done() and calls == ["POST", "GET", "DELETE"]
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError) as failure:
            await operation
        assert failure.value.preact_cleanup["request_accepted"]
        assert not failure.value.preact_cleanup["terminal_state_confirmed"]


async def test_failed_cleanup_is_not_silently_successful(inputs):
    verifier, request = inputs

    def handle(req):
        if req.method == "POST":
            return httpx.Response(202, json={"id": "fixture-job"})
        return httpx.Response(503, json={"detail": "fixture-secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        with pytest.raises(httpx.HTTPStatusError) as failure:
            await engine.predict(request)
        evidence = failure.value.preact_cleanup
        assert not evidence["request_accepted"]
        assert evidence["error"] == "HTTPStatusError" and evidence["http_status"] == 503
        assert "fixture-secret" not in json.dumps(evidence)


async def test_cleanup_has_an_independent_native_deadline(inputs):
    verifier, request = inputs
    request.deadline_seconds = 0.02
    calls = []

    async def handle(req):
        calls.append(req.method)
        if req.method == "POST":
            return httpx.Response(202, json={"id": "fixture-job"})
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        clock = time.monotonic()
        with pytest.raises(TimeoutError) as failure:
            await engine.predict(request)
        assert 4.8 < time.monotonic() - clock < 5.8
        assert calls == ["POST", "GET", "DELETE"]
        assert failure.value.preact_cleanup["error"] == "TimeoutError"
        assert not failure.value.preact_cleanup["request_accepted"]


@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
async def test_terminal_failures_are_not_predictions_or_retried(inputs, status):
    verifier, request = inputs
    calls = []

    def handle(req):
        calls.append(req.method)
        return httpx.Response(
            200,
            json={"id": "fixture-job"}
            if req.method == "POST"
            else {"status": status, "terminal_state_confirmed": True},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        with pytest.raises(EngineFailure) as failure:
            await engine.predict(request)
        assert calls == ["POST", "GET"]
        assert failure.value.preact_cleanup["terminal_state_confirmed"]
        assert failure.value.preact_cleanup["worker_status"] == status


async def test_completed_prediction_retains_job_evidence_without_cancellation(inputs):
    verifier, request = inputs
    prediction = await verifier.predict(request)
    calls = []

    def handle(req):
        calls.append(req.method)
        return httpx.Response(
            200,
            json={"id": "fixture-job"}
            if req.method == "POST"
            else {"status": "complete", "prediction": prediction.model_dump()},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        result, _ = await Registry([engine]).predict(engine, request)
        assert calls == ["POST", "GET"]
        assert result.raw["worker_job_id"] == "fixture-job"
        assert result.raw["worker_status"] == "complete"


@pytest.mark.parametrize("identifier", ["../jobs", "job?token=private", 123, ""])
async def test_invalid_job_identifiers_never_become_request_paths(inputs, identifier):
    verifier, request = inputs

    def handle(req):
        assert req.method == "POST"
        return httpx.Response(202, json={"id": identifier})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        with pytest.raises(EngineFailure, match="identifier") as failure:
            await engine.predict(request)
        assert failure.value.preact_cleanup["job_id"] is None


async def test_registry_timeout_preserves_wrapped_cleanup_evidence(inputs):
    verifier, request = inputs
    request.deadline_seconds = 0.02

    async def handle(req):
        if req.method == "POST":
            return httpx.Response(202, json={"id": "fixture-job"})
        if req.method == "DELETE":
            return httpx.Response(200, json={})
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        with pytest.raises(TimeoutError) as failure:
            await Registry([engine]).predict(engine, request)
        evidence = cleanup_evidence(failure.value)
        assert evidence["job_id"] == "fixture-job" and evidence["request_accepted"]
        assert not evidence["terminal_state_confirmed"]


async def test_runtime_durably_records_failed_remote_cleanup_without_execution(inputs, tmp_path):
    verifier, _ = inputs

    def handle(req):
        if req.method == "POST":
            return httpx.Response(202, json={"id": "fixture-job"})
        return httpx.Response(503, json={"detail": "fixture-secret"})

    store = Store("sqlite:///:memory:")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([engine]), Policy())
        await runtime.run(SoftwareWorld())
    events = store.read_events(runtime.run_id)
    failures = [e for e in events if e["kind"] == "engine_failed"]
    assert failures
    assert all(e["data"]["cleanup"]["http_status"] == 503 for e in failures)
    assert not any(e["kind"] == "execution_intent" for e in events)
    assert not store.error_rows()
    assert "fixture-secret" not in json.dumps(events)


async def test_runtime_records_remote_cancellation_acknowledgment_before_final_status(
    inputs, tmp_path
):
    verifier, _ = inputs
    polling = asyncio.Event()

    async def handle(req):
        if req.method == "POST":
            return httpx.Response(202, json={"id": "fixture-job"})
        if req.method == "DELETE":
            return httpx.Response(200, json={"status": "cancellation_requested"})
        polling.set()
        await asyncio.Event().wait()

    store = Store("sqlite:///:memory:")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = RemoteEngine("http://fixture", "fixture", verifier.capabilities, client)
        runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([engine]), Policy())
        operation = asyncio.create_task(runtime.run(SoftwareWorld()))
        await asyncio.wait_for(polling.wait(), 1)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
    events = store.read_events(runtime.run_id)
    cleanup = next(e["data"]["cleanup"] for e in events if e["kind"] == "engine_cancelled")
    assert cleanup["job_id"] == "fixture-job" and cleanup["request_accepted"]
    assert not cleanup["terminal_state_confirmed"]
    assert store.get_run(runtime.run_id)["status"] == "cancelled"
    assert not any(e["kind"] == "execution_intent" for e in events)


@pytest.mark.parametrize(
    "payload",
    [
        {"request_accepted": True, "terminal_state_confirmed": False, "response_body": "secret"},
        {"request_accepted": True, "terminal_state_confirmed": False, "job_id": "job?secret=value"},
        {"request_accepted": "yes", "terminal_state_confirmed": "yes"},
        {
            "request_accepted": True,
            "terminal_state_confirmed": False,
            "schema_version": "unbounded-fixture-version",
        },
    ],
)
def test_core_refuses_unbounded_cleanup_diagnostics(payload):
    error = EngineFailure("fixture")
    error.preact_cleanup = payload
    assert cleanup_evidence(error) is None
