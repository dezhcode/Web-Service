"""passenger_wsgi.application must serve the HTTP endpoints (Passenger is WSGI-only)."""
import io
import json
from wsgiref.util import setup_testing_defaults

import pytest

import app as app_module
from tests.test_app import FakeClient


def call(application, method, path, body=b"", headers=None):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(body),
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/json",
    }
    for k, v in (headers or {}).items():
        environ["HTTP_" + k.upper().replace("-", "_")] = v
    setup_testing_defaults(environ)
    status = {}

    def start_response(s, h, exc=None):
        status["s"], status["h"] = s, h

    out = b"".join(application(environ, start_response))
    return status["s"], dict(status["h"]), out


@pytest.fixture
def wsgi(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setattr(app_module, "_client", FakeClient())
    monkeypatch.setattr(app_module, "_limiter", None)
    import passenger_wsgi
    return passenger_wsgi.application


def test_health(wsgi):
    s, _, body = call(wsgi, "GET", "/health")
    assert s.startswith("200") and json.loads(body) == {"status": "ok"}


def test_chat_and_stream(wsgi):
    auth = {"Authorization": "Bearer secret"}
    payload = json.dumps({"prompt": "hi"}).encode()
    s, _, body = call(wsgi, "POST", "/api/chat", payload, auth)
    assert s.startswith("200") and json.loads(body) == {"text": "Hello"}
    s, h, body = call(wsgi, "POST", "/api/chat/stream", payload, auth)
    assert s.startswith("200") and h["content-type"].startswith("text/event-stream")
    assert body.count(b"data: ") == 3


def test_doc_and_websocket_flag(wsgi):
    s, h, body = call(wsgi, "GET", "/doc")
    assert s.startswith("200") and h["content-type"].startswith("text/html")
    s, _, body = call(wsgi, "GET", "/")
    assert json.loads(body)["websocket"] is False
