import base64
import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app as app_module
from copilot_client import CopilotError, CopilotSession


class FakeClient:
    def __init__(self):
        self.started = 0
        self.calls = []
        self.fail = None

    async def start_conversation(self):
        self.started += 1
        return CopilotSession(f"conv-{self.started}", "c", "s")

    @staticmethod
    def text_content(prompt):
        return [{"type": "text", "text": prompt}]

    async def image_content(self, image, prompt, session):
        return [{"type": "image", "bytes": len(image)}, {"type": "text", "text": prompt}]

    async def stream_chat(self, content, session):
        self.calls.append((session.conversation_id, content))
        if self.fail:
            raise CopilotError(self.fail)
        for chunk in ("Hel", "lo"):
            yield chunk


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    f = FakeClient()
    monkeypatch.setattr(app_module, "_client", f)
    monkeypatch.setattr(app_module, "_limiter", None)
    return f


@pytest.fixture
def client(fake):
    return TestClient(app_module.app)


AUTH = {"Authorization": "Bearer secret"}


def test_health_and_index_need_no_auth(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").json()["mode"] == "smart"


def test_auth_required(client, monkeypatch):
    assert client.post("/api/chat", json={"prompt": "x"}).status_code == 401
    assert client.post("/api/chat", json={"prompt": "x"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/api/chat", json={"prompt": "x"}, headers={"X-API-Key": "secret"}).status_code == 200
    assert client.post("/api/chat?api_key=secret", json={"prompt": "x"}).status_code == 200
    monkeypatch.setenv("API_KEY", "")
    assert client.post("/api/chat", json={"prompt": "x"}, headers=AUTH).status_code == 503


def test_json_chat(client, fake):
    r = client.post("/api/chat", json={"prompt": "hi"}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"text": "Hello"}
    assert fake.calls[0][1] == [{"type": "text", "text": "hi"}]


def test_json_chat_validation(client):
    assert client.post("/api/chat", json={}, headers=AUTH).status_code == 400
    assert client.post("/api/chat", json=[1], headers=AUTH).status_code == 400
    assert client.post("/api/chat", content=b"{bad", headers=AUTH).status_code == 400
    assert client.post("/api/chat", json={"prompt": "x" * 9000}, headers=AUTH).status_code == 400


def test_json_chat_upstream_error_is_502_without_details(client, fake):
    fake.fail = "secret internals"
    r = client.post("/api/chat", json={"prompt": "hi"}, headers=AUTH)
    assert r.status_code == 502 and "secret internals" not in r.text


def test_sse_stream(client):
    r = client.post("/api/chat/stream", json={"prompt": "hi"}, headers=AUTH)
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    assert events == [
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo"},
        {"type": "done"},
    ]


def test_sse_error_event(client, fake):
    fake.fail = "x"
    r = client.post("/api/chat/stream", json={"prompt": "hi"}, headers=AUTH)
    events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    assert events == [{"type": "error", "message": "upstream request failed"}]


def test_base64_image(client, fake):
    img = base64.b64encode(b"12345").decode()
    r = client.post("/api/chat", json={"image_base64": img}, headers=AUTH)
    assert r.status_code == 200
    content = fake.calls[0][1]
    assert content[0] == {"type": "image", "bytes": 5}
    assert content[1]["text"] == app_module.DEFAULT_IMAGE_PROMPT
    assert client.post("/api/chat", json={"image_base64": "***"}, headers=AUTH).status_code == 400


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x.png", "http://localhost/x.png", "http://169.254.169.254/latest",
    "http://10.0.0.1/x", "http://[::1]/x", "file:///etc/passwd", "ftp://example.com/x",
])
def test_image_url_ssrf_guard(client, url):
    r = client.post("/api/chat", json={"image_url": url}, headers=AUTH)
    assert r.status_code == 400


def test_ws_stream_and_multi_turn(client, fake):
    with client.websocket_connect("/ws?api_key=secret") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "chat", "id": "1", "prompt": "a"})
        assert ws.receive_json() == {"type": "delta", "id": "1", "text": "Hel"}
        assert ws.receive_json() == {"type": "delta", "id": "1", "text": "lo"}
        assert ws.receive_json() == {"type": "done", "id": "1", "text": "Hello"}

        ws.send_json({"id": "2", "prompt": "b"})  # type defaults to chat
        while ws.receive_json()["type"] != "done":
            pass
        assert [c[0] for c in fake.calls] == ["conv-1", "conv-1"]  # same conversation

        ws.send_json({"type": "reset"})
        assert ws.receive_json() == {"type": "reset"}
        ws.send_json({"type": "chat", "id": "3", "prompt": "c"})
        while ws.receive_json()["type"] != "done":
            pass
        assert fake.calls[-1][0] == "conv-2"  # new conversation after reset


def test_ws_errors_keep_connection_open(client, fake):
    with client.websocket_connect("/ws", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "chat", "id": "1"})
        assert ws.receive_json() == {"type": "error", "id": "1", "message": "prompt is required"}
        ws.send_json({"type": "bogus"})
        assert ws.receive_json()["type"] == "error"
        fake.fail = "x"
        ws.send_json({"type": "chat", "id": "2", "prompt": "hi"})
        assert ws.receive_json() == {"type": "error", "id": "2", "message": "upstream request failed"}
        fake.fail = None
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_ws_requires_auth(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass


def test_ws_path_over_plain_http_explains(client):
    r = client.get("/ws")
    assert r.status_code == 426


def test_doc_page(client):
    r = client.get("/doc")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "/api/chat/stream" in r.text and 'id="playground"' in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/docs").text == r.text


def test_root_redirects_browsers_to_doc(client):
    r = client.get("/", headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/doc"
    info = client.get("/", headers={"Accept": "application/json"}).json()
    assert info["docs"] == "/doc" and info["limits"]["max_prompt_chars"] == 8000


def test_root_reports_websocket_and_limits(client, monkeypatch):
    monkeypatch.setenv("WEBSOCKET_ENABLED", "0")
    monkeypatch.setenv("MAX_PROMPT_CHARS", "100")
    info = client.get("/").json()
    assert info["websocket"] is False and info["limits"]["max_prompt_chars"] == 100


def test_openapi(client):
    spec = client.get("/openapi.json", headers={"X-Forwarded-Proto": "https"}).json()
    assert spec["openapi"].startswith("3.")
    assert spec["servers"][0]["url"] == "https://testserver"
    assert {"/api/chat", "/api/chat/stream", "/health"} <= set(spec["paths"])
