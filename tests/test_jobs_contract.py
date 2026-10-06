import asyncio
import base64
import json
import time

import httpx
import pytest

from preact.engines.jobs import BatchSpec, NebiusJobs


async def test_serverless_payload_uses_official_fields_and_artifact_return_volume():
    client = NebiusJobs(token="unit-fixture", project="fixture-project")
    spec = BatchSpec(
        image="fixture@sha256:123",
        platform="gpu-l40s-a",
        preset="1gpu-8vcpu-32gb",
        subnet_id="fixture-subnet",
        output_volume="fixture-bucket",
    )
    bundle = {"engine": "isaac", "request": {"seed": 0}}
    payload = client.payload(bundle, spec)
    assert payload["metadata"]["parentId"] == "fixture-project"
    assert payload["spec"]["volumes"][0]["mode"] == "READ_WRITE"
    assert payload["spec"]["timeout"] == "3600s"
    assert json.loads(base64.b64decode(payload["spec"]["injectedFiles"][0]["content"])) == bundle
    assert "unit-fixture" not in json.dumps(payload)
    await client.aclose()


async def test_wait_deadline_covers_a_stalled_status_request():
    cancellations = []

    async def handle(request):
        if request.method == "GET":
            await asyncio.Event().wait()
        cancellations.append(json.loads(request.content)["id"])
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        jobs = NebiusJobs(token="fixture-token", project="fixture-project", client=client)
        clock = time.monotonic()
        with pytest.raises(TimeoutError) as failure:
            await jobs.wait("fixture-job", seconds=0.02)
        assert time.monotonic() - clock < 0.2
        assert cancellations == ["fixture-job"]
        assert failure.value.preact_cleanup == {
            "job_id": "fixture-job",
            "request_accepted": True,
            "terminal_state_confirmed": False,
        }


async def test_repeated_cancellation_drains_one_remote_cancel_request():
    polling, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = []

    async def handle(request):
        if request.method == "GET":
            polling.set()
            await asyncio.Event().wait()
        cancellations.append(json.loads(request.content)["id"])
        cancelling.set()
        await release.wait()
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        jobs = NebiusJobs(token="fixture-token", project="fixture-project", client=client)
        operation = asyncio.create_task(jobs.wait("fixture-job", seconds=5))
        try:
            await asyncio.wait_for(polling.wait(), 1)
            operation.cancel()
            await asyncio.wait_for(cancelling.wait(), 1)
            operation.cancel()
            await asyncio.sleep(0.01)
            assert not operation.done() and cancellations == ["fixture-job"]
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError) as failure:
            await operation
        assert failure.value.preact_cleanup["request_accepted"]
        assert not failure.value.preact_cleanup["terminal_state_confirmed"]


@pytest.mark.parametrize("fault", ["http", "malformed"])
async def test_failed_poll_requests_cleanup_without_becoming_a_success(fault):
    cancellations = []

    def handle(request):
        if request.method == "GET":
            return (
                httpx.Response(503, json={"detail": "fixture-secret"})
                if fault == "http"
                else httpx.Response(200, json={})
            )
        cancellations.append(request.method)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        jobs = NebiusJobs(token="fixture-token", project="fixture-project", client=client)
        with pytest.raises(httpx.HTTPStatusError if fault == "http" else KeyError) as failure:
            await jobs.wait("fixture-job", seconds=1)
        assert cancellations == ["POST"]
        assert failure.value.preact_cleanup["request_accepted"]
        assert "fixture-secret" not in json.dumps(failure.value.preact_cleanup)


async def test_cancel_denial_stays_unconfirmed_and_preserves_original_failure():
    async def handle(request):
        if request.method == "GET":
            await asyncio.Event().wait()
        return httpx.Response(403, json={"detail": "fixture-secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        jobs = NebiusJobs(token="fixture-token", project="fixture-project", client=client)
        with pytest.raises(TimeoutError) as failure:
            await jobs.wait("fixture-job", seconds=0.02)
        assert failure.value.preact_cleanup == {
            "job_id": "fixture-job",
            "request_accepted": False,
            "error": "HTTPStatusError",
            "http_status": 403,
            "terminal_state_confirmed": False,
        }


async def test_stalled_cleanup_has_a_real_native_async_deadline():
    calls = []

    async def handle(request):
        calls.append(request.method)
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        jobs = NebiusJobs(token="fixture-token", project="fixture-project", client=client)
        clock = time.monotonic()
        with pytest.raises(TimeoutError) as failure:
            await jobs.wait("fixture-job", seconds=0.02)
        elapsed = time.monotonic() - clock
        assert 4.8 < elapsed < 5.8
        assert calls == ["GET", "POST"]
        assert failure.value.preact_cleanup["error"] == "TimeoutError"
        assert not failure.value.preact_cleanup["request_accepted"]


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELED", "CANCELLED"])
async def test_terminal_job_status_is_preserved_without_inventing_prediction(state):
    def handle(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"status": {"state": state}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        jobs = NebiusJobs(token="fixture-token", project="fixture-project", client=client)
        assert await jobs.wait("fixture-job", seconds=1) == {"status": {"state": state}}


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan")])
async def test_invalid_budget_is_rejected_before_remote_requests(seconds):
    def handle(request):
        raise AssertionError("Invalid budgets must not make requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        jobs = NebiusJobs(token="fixture-token", project="fixture-project", client=client)
        with pytest.raises(ValueError):
            await jobs.wait("fixture-job", seconds=seconds)


async def test_owned_clients_close_and_borrowed_clients_remain_open(monkeypatch):
    client = httpx.AsyncClient()
    borrowed = NebiusJobs(token="fixture-token", project="fixture-project", client=client)
    await borrowed.aclose()
    assert not client.is_closed
    monkeypatch.setattr("preact.engines.jobs.httpx.AsyncClient", lambda: client)
    async with NebiusJobs(token="fixture-token", project="fixture-project") as owned:
        assert owned.client is client
    assert client.is_closed
