import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import websockets
from websockets.asyncio.server import serve

from copilot_client import _hashcash_ok
import hashlib

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"


class FakeCopilot:
    """Local stand-in for copilot.microsoft.com: /c/api/start, /c/api/attachments, WS /c/api/chat."""

    def __init__(self):
        self.sent = []          # "send" events received
        self.uploads = []       # attachment bodies
        self.challenge_ok = None
        self.fail_with = None   # emit an error event instead of text
        self.chunks = ["Hel", "lo"]

    async def _ws_handler(self, ws):
        assert "/c/api/chat" in ws.request.path
        assert ws.request.headers["Cookie"] == "sid=abc"
        await ws.recv()  # setOptions
        await ws.recv()  # reportLocalConsents
        send = json.loads(await ws.recv())
        self.sent.append(send)
        seed = "seed"
        await ws.send(json.dumps({"event": "challenge", "method": "hashcash", "parameter": f"{seed}:10"}))
        resp = json.loads(await ws.recv())
        digest = hashlib.sha256(f"{seed}{resp['token']}".encode()).digest()
        self.challenge_ok = resp["event"] == "challengeResponse" and _hashcash_ok(digest, 10)
        if self.fail_with:
            await ws.send(json.dumps({"event": "error", "detail": self.fail_with}))
            return
        for c in self.chunks:
            await ws.send(json.dumps({"event": "appendText", "text": c}))
        await ws.send(json.dumps({"event": "done"}))


@pytest.fixture
def fake_copilot():
    fake = FakeCopilot()
    ready = threading.Event()
    state = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path == "/c/api/start":
                out = json.dumps({"currentConversationId": "conv-1"}).encode()
                self.send_response(200)
                self.send_header("Set-Cookie", "sid=abc")
            elif self.path == "/c/api/attachments":
                fake.uploads.append(body)
                out = json.dumps({"url": "https://files/x.png"}).encode()
                self.send_response(200)
            else:
                self.send_response(404)
                out = b"{}"
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def run_ws():
        loop = asyncio.new_event_loop()
        state["loop"] = loop

        async def main():
            async with serve(fake._ws_handler, "127.0.0.1", 0) as server:
                state["port"] = server.sockets[0].getsockname()[1]
                state["stop"] = asyncio.Event()
                ready.set()
                await state["stop"].wait()

        loop.run_until_complete(main())

    t = threading.Thread(target=run_ws, daemon=True)
    t.start()
    assert ready.wait(5)
    fake.http_base = f"http://127.0.0.1:{httpd.server_port}"
    fake.ws_base = f"ws://127.0.0.1:{state['port']}"
    yield fake
    state["loop"].call_soon_threadsafe(state["stop"].set)
    httpd.shutdown()
