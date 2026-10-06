"""Mock transport conformance only: these reports are not live sponsor evidence."""

import json

import httpx

from preact.integration_validation import validate_token_factory


async def test_missing_credentials_never_attempts_network_or_invents_a_model():
    def reject(request):
        raise AssertionError("Network must not run")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
        report = await validate_token_factory(None, None, client)
    assert report["status"] == "blocked" and report["catalog"] is None
    assert report["inference"] == []


async def test_unlisted_model_never_runs_inference():
    requests = []

    def catalog(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "fixture-admitted"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(catalog)) as client:
        report = await validate_token_factory("invented-id", "fixture-key", client)
    assert requests == ["/v1/models"]
    assert report["status"] == "blocked" and report["inference"] == []


async def test_admitted_model_uses_both_domain_contracts_and_preserves_nonsecret_evidence():
    domains = []

    def handle(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "fixture-admitted"}]})
        body = json.loads(request.content)
        data = json.loads(body["messages"][1]["content"])
        domains.append(data["request"]["state"]["domain"])
        return httpx.Response(
            200,
            json={
                "id": "fixture-inference-id",
                "model": "fixture-admitted",
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "success": {"value": 0.9, "measured": True},
                                    "risk": {"value": 0.1, "measured": True},
                                    "assumptions": ["Unit transport fixture"],
                                }
                            ),
                        },
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report = await validate_token_factory("fixture-admitted", "fixture-key", client)
    assert domains == ["software", "physical"] and report["status"] == "inference_verified"
    assert all(not entry["prediction"]["success"]["measured"] for entry in report["inference"])
    assert "fixture-key" not in json.dumps(report)
    assert all(not entry["cached"] for entry in report["inference"])


async def test_http_failure_does_not_serialize_service_secret_or_become_success():
    def denied(request):
        return httpx.Response(403, json={"detail": "fixture-secret-in-error-response"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(denied)) as client:
        report = await validate_token_factory("fixture-admitted", "fixture-key", client)
    assert report["status"] == "failed" and report["failure"]["http_status"] == 403
    assert "fixture-secret" not in json.dumps(report)
