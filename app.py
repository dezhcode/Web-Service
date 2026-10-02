# Web service exposing CopilotClient (smart mode) over:
#   WebSocket  /ws               - streaming, multi-turn per connection (needs an ASGI server)
#   SSE        /api/chat/stream  - streaming over plain HTTP (works under Passenger/WSGI)
#   JSON       /api/chat         - single request/response
#
# ASGI:  uvicorn app:app
# WSGI:  see passenger_wsgi.py (HTTP endpoints only, WebSocket is not possible there)

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import ipaddress
import json
import logging
import os
import socket
from typing import AsyncIterator, Optional
from urllib.parse import urljoin, urlparse

import requests
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from copilot_client import CopilotClient, CopilotSession

log = logging.getLogger("copilot-service")


def _load_dotenv(path: str) -> None:
    """Minimal .env loader for hosts where env vars can't be set in a panel."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except OSError:
        pass


_load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Read at call time (not import time) so tests and panels can change them.
def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


DEFAULT_IMAGE_PROMPT = "این تصویر چیست؟ دقیق توضیح بده."
MAX_REDIRECTS = 3


class BadRequest(Exception):
    pass


# ───────────────────────────── Auth ──────────────────────────────────────────

def _api_keys() -> list[str]:
    return [k.strip() for k in _env("API_KEY", "").split(",") if k.strip()]


def _authorized(headers, query) -> bool:
    keys = _api_keys()
    if not keys:
        return False  # fail closed: the service must not be open by accident
    supplied = headers.get("x-api-key") or ""
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    supplied = supplied or query.get("api_key") or ""
    return any(hmac.compare_digest(supplied.encode(), k.encode()) for k in keys)


def _deny_response() -> JSONResponse:
    if not _api_keys():
        return JSONResponse({"error": "API_KEY is not configured on the server"}, status_code=503)
    return JSONResponse({"error": "unauthorized"}, status_code=401)


# ───────────────────────────── Request parsing ───────────────────────────────

def _check_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BadRequest("image_url must be an http(s) URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BadRequest("image_url host could not be resolved")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise BadRequest("image_url must point to a public host")


def _download_image(url: str, max_bytes: int) -> bytes:
    # Redirects are followed by hand so every hop is checked (SSRF guard).
    for _ in range(MAX_REDIRECTS + 1):
        _check_public_url(url)
        try:
            with requests.get(url, timeout=15, stream=True, allow_redirects=False) as r:
                if r.is_redirect:
                    location = r.headers.get("location")
                    if not location:
                        raise BadRequest("image_url redirect without location")
                    url = urljoin(url, location)
                    continue
                r.raise_for_status()
                data = bytearray()
                for chunk in r.iter_content(64 * 1024):
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise BadRequest("image is too large")
                return bytes(data)
        except requests.RequestException:
            raise BadRequest("image_url could not be downloaded")
    raise BadRequest("image_url has too many redirects")


async def parse_chat_request(body) -> tuple[str, Optional[bytes]]:
    """Validate a chat payload; returns (prompt, image_bytes or None)."""
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    prompt = body.get("prompt")
    image_url = body.get("image_url")
    image_b64 = body.get("image_base64")

    if prompt is not None and not isinstance(prompt, str):
        raise BadRequest("prompt must be a string")
    prompt = (prompt or "").strip()
    has_image = bool(image_url or image_b64)
    if not prompt and not has_image:
        raise BadRequest("prompt is required")
    if len(prompt) > int(_env("MAX_PROMPT_CHARS", "8000")):
        raise BadRequest("prompt is too long")

    if not has_image:
        return prompt, None

    max_bytes = int(_env("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
    if image_b64:
        if not isinstance(image_b64, str):
            raise BadRequest("image_base64 must be a string")
        if image_b64.startswith("data:"):
            image_b64 = image_b64.partition(",")[2]
        try:
            image = base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError):
            raise BadRequest("image_base64 is not valid base64")
        if len(image) > max_bytes:
            raise BadRequest("image is too large")
    else:
        if not isinstance(image_url, str):
            raise BadRequest("image_url must be a string")
        image = await asyncio.to_thread(_download_image, image_url, max_bytes)

    return prompt or DEFAULT_IMAGE_PROMPT, image


# ───────────────────────────── Chat engine ───────────────────────────────────

_client: Optional[CopilotClient] = None
_limiter: Optional[asyncio.Semaphore] = None


def get_client() -> CopilotClient:
    global _client
    if _client is None:
        _client = CopilotClient(mode=_env("COPILOT_MODE", "smart"))
    return _client


def _get_limiter() -> asyncio.Semaphore:
    # Created lazily so it binds to the running event loop.
    global _limiter
    if _limiter is None:
        _limiter = asyncio.Semaphore(int(_env("MAX_CONCURRENCY", "3")))
    return _limiter


class Conversation:
    """One Copilot conversation; reused across messages of a WebSocket connection."""

    def __init__(self) -> None:
        self.session: Optional[CopilotSession] = None


async def run_chat(prompt: str, image: Optional[bytes], conv: Conversation) -> AsyncIterator[str]:
    client = get_client()
    async with _get_limiter():
        if conv.session is None:
            conv.session = await client.start_conversation()
        if image is not None:
            content = await client.image_content(image, prompt, conv.session)
        else:
            content = client.text_content(prompt)
        async for chunk in client.stream_chat(content, conv.session):
            yield chunk


def _error_message(exc: Exception) -> str:
    if isinstance(exc, BadRequest):
        return str(exc)
    log.exception("copilot request failed")
    return "upstream request failed"


# ───────────────────────────── HTTP endpoints ────────────────────────────────

async def index(request: Request) -> JSONResponse:
    return JSONResponse({
        "service": "copilot-web-service",
        "mode": _env("COPILOT_MODE", "smart"),
        "endpoints": {
            "ws": "/ws (WebSocket, ASGI hosts only)",
            "sse": "POST /api/chat/stream",
            "json": "POST /api/chat",
        },
    })


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _read_json(request: Request):
    try:
        return await request.json()
    except ValueError:
        raise BadRequest("body must be valid JSON")


async def api_chat(request: Request) -> JSONResponse:
    if not _authorized(request.headers, request.query_params):
        return _deny_response()
    gen = None
    try:
        prompt, image = await parse_chat_request(await _read_json(request))
        parts: list[str] = []
        gen = run_chat(prompt, image, Conversation())
        try:
            async for chunk in gen:
                parts.append(chunk)
        except Exception:
            if not parts:  # keep partial output, like the original script
                raise
        return JSONResponse({"text": "".join(parts).strip()})
    except BadRequest as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": _error_message(e)}, status_code=502)
    finally:
        if gen is not None:
            await gen.aclose()


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


async def api_chat_stream(request: Request):
    if not _authorized(request.headers, request.query_params):
        return _deny_response()
    try:
        prompt, image = await parse_chat_request(await _read_json(request))
    except BadRequest as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    async def events() -> AsyncIterator[bytes]:
        yield b": ok\n\n"  # flush headers early
        gen = run_chat(prompt, image, Conversation())
        try:
            async for chunk in gen:
                yield _sse({"type": "delta", "text": chunk})
            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": _error_message(e)})
        finally:
            await gen.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def ws_unavailable(request: Request) -> JSONResponse:
    return JSONResponse(
        {"error": "this endpoint needs a WebSocket connection",
         "hint": "use POST /api/chat/stream (SSE) if WebSocket is not available on this host"},
        status_code=426,
    )


# ───────────────────────────── WebSocket endpoint ────────────────────────────
#
# client → server  {"type":"chat","id":"1","prompt":"...","image_url"|"image_base64":"..."}
#                  {"type":"reset"}   start a fresh conversation
#                  {"type":"ping"}
# server → client  {"type":"delta","id":"1","text":"..."}   (repeated)
#                  {"type":"done","id":"1","text":"<full reply>"}
#                  {"type":"error","id":"1","message":"..."}
#                  {"type":"ready"} | {"type":"reset"} | {"type":"pong"}

async def _ws_chat_message(ws: WebSocket, conv: Conversation, msg: dict) -> None:
    msg_id = msg.get("id")
    gen = None
    try:
        prompt, image = await parse_chat_request(msg)
        parts: list[str] = []
        gen = run_chat(prompt, image, conv)
        async for chunk in gen:
            parts.append(chunk)
            await ws.send_json({"type": "delta", "id": msg_id, "text": chunk})
        await ws.send_json({"type": "done", "id": msg_id, "text": "".join(parts).strip()})
    except (WebSocketDisconnect, RuntimeError):
        raise
    except Exception as e:
        await ws.send_json({"type": "error", "id": msg_id, "message": _error_message(e)})
    finally:
        if gen is not None:
            await gen.aclose()


async def ws_chat(ws: WebSocket) -> None:
    if not _authorized(ws.headers, ws.query_params):
        await ws.close(code=1008)
        return
    await ws.accept()
    conv = Conversation()
    await ws.send_json({"type": "ready", "mode": _env("COPILOT_MODE", "smart")})
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError
            except ValueError:
                await ws.send_json({"type": "error", "message": "message must be a JSON object"})
                continue

            kind = msg.get("type", "chat")
            if kind == "chat":
                await _ws_chat_message(ws, conv, msg)
            elif kind == "reset":
                conv = Conversation()
                await ws.send_json({"type": "reset"})
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
            else:
                await ws.send_json({"type": "error", "id": msg.get("id"),
                                    "message": f"unknown message type: {kind}"})
    except (WebSocketDisconnect, RuntimeError):
        return  # client went away (RuntimeError: send after close)


# ───────────────────────────── App ───────────────────────────────────────────

def create_app() -> Starlette:
    middleware = []
    origins = [o.strip() for o in _env("CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:
        middleware.append(Middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["POST", "GET", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key"],
        ))
    return Starlette(
        routes=[
            Route("/", index),
            Route("/health", health),
            Route("/api/chat", api_chat, methods=["POST"]),
            Route("/api/chat/stream", api_chat_stream, methods=["POST"]),
            Route("/ws", ws_unavailable),
            WebSocketRoute("/ws", ws_chat),
        ],
        middleware=middleware,
    )


app = create_app()
