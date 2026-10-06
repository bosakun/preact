"""Actual ASGI disconnect delivery must stop replay without changing the run."""

from preact.service.app import create_app


async def test_disconnected_archive_stream_stops_before_draining_remaining_events(tmp_path):
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    store = app.state.store
    run_id = store.create_run({"scope": "disconnect regression"})
    for index in range(100):
        store.append(run_id, "probe", {"index": index})
    store.set_status(run_id, "complete", {"success": True})
    disconnected = False
    chunks = []

    async def receive():
        return (
            {"type": "http.disconnect"}
            if disconnected
            else {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }
        )

    async def send(message):
        nonlocal disconnected
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            if len(chunks) == 3:
                disconnected = True

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/api/runs/{run_id}/stream",
        "raw_path": f"/api/runs/{run_id}/stream".encode(),
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("test", 123),
        "root_path": "",
    }
    await app(scope, receive, send)
    assert len(chunks) == 3
    assert len(store.read_events(run_id)) == 100
    assert store.get_run(run_id)["status"] == "complete"
    assert not store.pending_execution(run_id)
