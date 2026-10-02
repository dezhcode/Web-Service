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


def test_setup_script_public_url_checks(server, capsys, monkeypatch):
    import importlib
    import setup_and_test as s
    importlib.reload(s)
    monkeypatch.setattr(s, "CHAT_TIMEOUT", 30)
    s.step_public(f"http://127.0.0.1:{server}", "secret", True)
    out = capsys.readouterr().out
    assert "[FAIL]" not in out, out
    assert "[OK  ] GET /health -> 200" in out
    assert "[OK  ] request without key -> 401" in out
    assert "[OK  ] POST /api/chat returns a reply" in out
    assert "[OK  ] POST /api/chat/stream delivers deltas + done" in out
    assert "plain http in use" in out  # warns about http


def test_setup_script_public_url_wrong_key(server, capsys):
    import importlib
    import setup_and_test as s
    importlib.reload(s)
    s.step_public(f"http://127.0.0.1:{server}", "not-the-key", True)
    out = capsys.readouterr().out
    assert "[FAIL] POST /api/chat returns a reply" in out


def _ask(server, *args, key="secret", extra_env=None, cwd=None):
    import os
    import subprocess
    from pathlib import Path
    script = Path(__file__).resolve().parent.parent / "ask.sh"
    env = dict(os.environ, ASK_URL=f"http://127.0.0.1:{server}")
    env.pop("API_KEY", None)
    if key is not None:
        env["API_KEY"] = key
    env.update(extra_env or {})
    return subprocess.run([str(script), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, env=env, timeout=60, cwd=cwd)


def test_ask_sh_reply(server):
    p = _ask(server, "hi", "there")
    assert p.returncode == 0, p.stderr
    assert p.stdout == "Hello\n"


def test_ask_sh_stream(server):
    p = _ask(server, "-s", "hi")
    assert p.returncode == 0, p.stderr
    assert p.stdout == "Hello\n"


def test_ask_sh_wrong_key(server):
    p = _ask(server, "hi", key="nope")
    assert p.returncode == 1 and "unauthorized" in p.stderr and p.stdout == ""
    p = _ask(server, "-s", "hi", key="nope")
    assert p.returncode == 1 and "unauthorized" in p.stderr


def test_ask_sh_quotes_in_prompt_and_bad_image(server):
    assert _ask(server, 'say "hi" and \'bye\' $HOME `x`').returncode == 0
    p = _ask(server, "-i", "http://127.0.0.1/x.png", "what")
    assert p.returncode == 1 and "public host" in p.stderr


def test_ask_sh_reads_key_from_env_file(server, tmp_path):
    import shutil
    from pathlib import Path
    shutil.copy(Path(__file__).resolve().parent.parent / "ask.sh", tmp_path / "ask.sh")
    (tmp_path / ".env").write_text('COPILOT_MODE=smart\nAPI_KEY="secret"\n')
    import os, subprocess
    env = dict(os.environ, ASK_URL=f"http://127.0.0.1:{server}")
    env.pop("API_KEY", None)
    p = subprocess.run([str(tmp_path / "ask.sh"), "hi"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, env=env, timeout=60)
    assert p.returncode == 0 and p.stdout == "Hello\n", p.stderr


def test_ask_sh_errors(server, tmp_path):
    import os
    import shutil
    import subprocess
    from pathlib import Path
    assert _ask(server).returncode == 2  # no prompt -> usage
    p = _ask(server, "-u", "http://127.0.0.1:1", "hi")
    assert p.returncode == 1 and "cannot reach" in p.stderr

    # no key in the environment and no .env next to the script
    shutil.copy(Path(__file__).resolve().parent.parent / "ask.sh", tmp_path / "ask.sh")
    env = dict(os.environ, ASK_URL=f"http://127.0.0.1:{server}")
    env.pop("API_KEY", None)
    p = subprocess.run([str(tmp_path / "ask.sh"), "hi"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, env=env, timeout=60)
    assert p.returncode == 1 and "no API key" in p.stderr
