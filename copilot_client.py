# Copilot client: the protocol logic of the original copilot_cli.py, refactored so
# replies can be streamed chunk by chunk (needed for the WebSocket / SSE endpoints).

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import requests
import websockets

HTTP_BASE = "https://copilot.microsoft.com"
WS_BASE = "wss://copilot.microsoft.com"


class CopilotError(Exception):
    pass


# ───────────────────────────── Challenge solvers ─────────────────────────────

def _hashcash_ok(digest: bytes, difficulty: int) -> bool:
    full, rem = divmod(difficulty, 8)
    for i in range(full):
        if digest[i] != 0:
            return False
    if rem:
        mask = (0xFF << (8 - rem)) & 0xFF
        if digest[full] & mask != 0:
            return False
    return True


def solve_hashcash(parameter: str) -> str:
    seed, diff_str = parameter.rsplit(":", 1)
    difficulty = int(diff_str)
    # Expected work is 2**difficulty; give up far beyond that instead of spinning forever.
    limit = 64 * 2 ** difficulty
    n = 0
    while n < limit:
        digest = hashlib.sha256(f"{seed}{n}".encode()).digest()
        if _hashcash_ok(digest, difficulty):
            return str(n)
        n += 1
    raise CopilotError("hashcash challenge could not be solved")


def solve_copilot_challenge(parameter: str) -> str:
    a = float(parameter)
    return str(int(math.floor(((a ** 3 / 100 + a * 25) % 22) + 0.5)))


# ───────────────────────────── Client ────────────────────────────────────────

@dataclass(frozen=True)
class CopilotSession:
    conversation_id: str
    cookie: str
    client_session_id: str


class CopilotClient:
    def __init__(
        self,
        mode: str = "smart",
        timeout: float = 70.0,
        http_base: str = HTTP_BASE,
        ws_base: str = WS_BASE,
    ):
        self._user_agent = "CopilotNative/30.0.430320002 (Android 9; samsung; SM-G988N)"
        self._mode = mode
        self._timeout = timeout
        self._http_base = http_base.rstrip("/")
        self._ws_base = ws_base.rstrip("/")

    async def start_conversation(self) -> CopilotSession:
        url = f"{self._http_base}/c/api/start"
        payload = {
            "timeZone": "Asia/Tehran",
            "startNewConversation": True,
            "teenSupportEnabled": False,
        }
        headers = {
            "User-Agent": self._user_agent,
            "x-search-uilang": "en-US",
            "Content-Type": "application/json",
        }

        def _req():
            return requests.post(url, json=payload, headers=headers, timeout=15)

        resp = await asyncio.to_thread(_req)
        resp.raise_for_status()
        data = resp.json()

        conversation_id = data.get("currentConversationId")
        cookie = resp.headers.get("set-cookie") or resp.headers.get("Set-Cookie") or ""

        if not conversation_id:
            raise CopilotError("failed to start conversation")

        return CopilotSession(
            conversation_id=conversation_id,
            cookie=cookie,
            client_session_id=str(uuid.uuid4()),
        )

    async def upload_image(self, image_bytes: bytes, cookie: str) -> str:
        headers = {
            "User-Agent": self._user_agent,
            "Content-Type": "image/png",
            "Cookie": cookie,
        }

        def _req():
            return requests.post(
                f"{self._http_base}/c/api/attachments",
                headers=headers,
                data=image_bytes,
                timeout=30,
            )

        resp = await asyncio.to_thread(_req)
        resp.raise_for_status()
        url = resp.json().get("url")
        if not url:
            raise CopilotError("image upload failed")
        return url

    def _connect(self, session: CopilotSession):
        ws_url = (
            f"{self._ws_base}/c/api/chat"
            f"?api-version=2&clientSessionId={session.client_session_id}"
        )
        headers = {"User-Agent": self._user_agent, "Cookie": session.cookie}
        try:
            return websockets.connect(ws_url, additional_headers=headers, open_timeout=20)
        except TypeError:
            return websockets.connect(ws_url, extra_headers=headers, open_timeout=20)

    async def stream_chat(self, content: list[dict], session: CopilotSession) -> AsyncIterator[str]:
        """Yield reply text chunks as Copilot produces them.

        Connection failures are retried only while nothing has been yielded yet;
        a failure after partial output is raised to the caller.
        """
        last_error: Optional[Exception] = None
        yielded = False

        for attempt in range(3):
            try:
                async with self._connect(session) as ws:
                    await ws.send(json.dumps({
                        "event": "setOptions",
                        "supportedFeatures": ["partial-generated-images"],
                        "supportedCards": ["image"],
                        "supportedUIComponents": {},
                        "ads": {"supportedTypes": ["text"]},
                        "supportedActions": [],
                    }))
                    await ws.send(json.dumps({
                        "event": "reportLocalConsents",
                        "grantedConsents": [],
                    }))
                    await ws.send(json.dumps({
                        "event": "send",
                        "content": content,
                        "context": {},
                        "conversationId": session.conversation_id,
                        "mode": self._mode,
                    }))

                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                        data = json.loads(raw)
                        event = data.get("event")

                        if event == "challenge":
                            method = data.get("method")
                            parameter = data.get("parameter") or ""
                            token = None
                            if method == "hashcash" and parameter:
                                token = await asyncio.to_thread(solve_hashcash, parameter)
                            elif method == "copilot" and parameter:
                                token = solve_copilot_challenge(parameter)
                            if token:
                                await ws.send(json.dumps({
                                    "event": "challengeResponse",
                                    "token": token,
                                    "method": method,
                                }))

                        elif event == "appendText" and data.get("text"):
                            yielded = True
                            yield data["text"]

                        elif event == "done":
                            return

                        elif event == "error":
                            raise CopilotError(f"copilot error: {data}")

            except Exception as e:
                last_error = e
                if yielded:
                    raise
                await asyncio.sleep(0.8 * (attempt + 1))

        raise last_error or CopilotError("could not connect to copilot")

    async def ask(self, content: list[dict], session: Optional[CopilotSession] = None) -> str:
        """Collect the full reply. A failure after partial output returns what was received."""
        session = session or await self.start_conversation()
        parts: list[str] = []
        try:
            async for chunk in self.stream_chat(content, session):
                parts.append(chunk)
        except Exception:
            if not parts:
                raise
        return "".join(parts).strip()

    @staticmethod
    def text_content(prompt: str) -> list[dict]:
        return [{"type": "text", "text": prompt}]

    async def image_content(
        self, image_bytes: bytes, prompt: str, session: CopilotSession
    ) -> list[dict]:
        uploaded_url = await self.upload_image(image_bytes, session.cookie)
        return [
            {"type": "image", "url": uploaded_url},
            {"type": "text", "text": prompt},
        ]
