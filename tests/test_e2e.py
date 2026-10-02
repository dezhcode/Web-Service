"""Real network path: websockets client → uvicorn → app → CopilotClient → fake Copilot."""
import json
import threading
import time

import pytest
import uvicorn
import websockets

import app as app_module
from copilot_client import CopilotClient


@pytest.fixture
def server(fake_copilot, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setattr(app_module, "_limiter", None)
    monkeypatch.setattr(app_module, "_client", CopilotClient(
        http_base=fake_copilot.http_base, ws_base=fake_copilot.ws_base))
    config = uvicorn.Config(app_module.app, host="127.0.0.1", port=0, log_level="warning")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.02)
    yield srv.servers[0].sockets[0].getsockname()[1]
    srv.should_exit = True
    t.join(5)


async def test_websocket_end_to_end(server, fake_copilot):
    async with websockets.connect(
        f"ws://127.0.0.1:{server}/ws", additional_headers={"Authorization": "Bearer secret"}
    ) as ws:
        assert json.loads(await ws.recv())["type"] == "ready"
        await ws.send(json.dumps({"type": "chat", "id": "1", "prompt": "hi"}))
        msgs = []
        while True:
            m = json.loads(await ws.recv())
            msgs.append(m)
            if m["type"] in ("done", "error"):
                break
    assert [m["type"] for m in msgs] == ["delta", "delta", "done"]
    assert msgs[-1]["text"] == "Hello"
    assert fake_copilot.sent[0]["mode"] == "smart"


async def test_websocket_rejects_missing_key(server):
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with websockets.connect(f"ws://127.0.0.1:{server}/ws"):
            pass
