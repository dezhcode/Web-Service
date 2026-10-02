import pytest

from copilot_client import (
    CopilotClient, CopilotError, _hashcash_ok, solve_copilot_challenge, solve_hashcash,
)
import hashlib


def test_hashcash_solution_meets_difficulty():
    token = solve_hashcash("some-seed:12")
    digest = hashlib.sha256(f"some-seed{token}".encode()).digest()
    assert _hashcash_ok(digest, 12)


def test_copilot_challenge_formula():
    # (10**3/100 + 10*25) % 22 = 18
    assert solve_copilot_challenge("10") == "18"


async def test_ask_streams_and_solves_challenge(fake_copilot):
    client = CopilotClient(http_base=fake_copilot.http_base, ws_base=fake_copilot.ws_base)
    session = await client.start_conversation()
    assert session.conversation_id == "conv-1" and session.cookie == "sid=abc"

    chunks = [c async for c in client.stream_chat(client.text_content("hi"), session)]
    assert chunks == ["Hel", "lo"]
    assert fake_copilot.challenge_ok
    sent = fake_copilot.sent[0]
    assert sent["mode"] == "smart"
    assert sent["conversationId"] == "conv-1"
    assert sent["content"] == [{"type": "text", "text": "hi"}]


async def test_image_content_uploads_first(fake_copilot):
    client = CopilotClient(http_base=fake_copilot.http_base, ws_base=fake_copilot.ws_base)
    session = await client.start_conversation()
    content = await client.image_content(b"\x89PNGdata", "what?", session)
    assert fake_copilot.uploads == [b"\x89PNGdata"]
    assert content == [
        {"type": "image", "url": "https://files/x.png"},
        {"type": "text", "text": "what?"},
    ]


async def test_error_event_is_raised_after_retries(fake_copilot, monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    fake_copilot.fail_with = "boom"
    client = CopilotClient(http_base=fake_copilot.http_base, ws_base=fake_copilot.ws_base)
    with pytest.raises(CopilotError):
        await client.ask(client.text_content("hi"))


async def _no_sleep(*_a, **_k):
    return None
