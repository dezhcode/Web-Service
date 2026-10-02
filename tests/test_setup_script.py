"""Runs setup_and_test.py as a subprocess against the fake Copilot server."""
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "setup_and_test.py"


def run(app_dir, fake_http, fake_ws, *extra):
    env = dict(os.environ, COPILOT_HTTP_BASE=fake_http, COPILOT_WS_BASE=fake_ws)
    env.pop("API_KEY", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--no-install", "--no-url", "--app-dir", str(app_dir), *extra],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=env, timeout=120,
    )


def key_from(output):
    return output.split("Your API key (keep it secret) ==")[1].strip().splitlines()[0]


def test_full_run_creates_env_and_passes(fake_copilot, tmp_path):
    p = run(tmp_path, fake_copilot.http_base, fake_copilot.ws_base)
    assert p.returncode == 0, p.stdout
    assert "[FAIL]" not in p.stdout
    assert "POST /api/chat returns a reply" in p.stdout
    assert "POST /api/chat/stream streams deltas + done" in p.stdout
    key = key_from(p.stdout)
    env = (tmp_path / ".env").read_text()
    assert f"API_KEY={key}" in env and "COPILOT_MODE=smart" in env
    assert oct((tmp_path / ".env").stat().st_mode & 0o777) == "0o600"
    assert (tmp_path / "tmp" / "restart.txt").exists()


def test_existing_key_kept_and_rotate_replaces(fake_copilot, tmp_path):
    first = key_from(run(tmp_path, fake_copilot.http_base, fake_copilot.ws_base).stdout)
    second = key_from(run(tmp_path, fake_copilot.http_base, fake_copilot.ws_base).stdout)
    assert first == second
    third = key_from(run(tmp_path, fake_copilot.http_base, fake_copilot.ws_base, "--rotate").stdout)
    assert third != first
    assert (tmp_path / ".env").read_text().count("API_KEY=") == 1


def test_unreachable_copilot_fails_clearly_and_skips_chat(tmp_path):
    p = run(tmp_path, "http://127.0.0.1:9", "ws://127.0.0.1:9")
    assert p.returncode == 1
    assert "[FAIL] cannot connect to Copilot" in p.stdout
    assert "Real chat tests" not in p.stdout
    assert "[OK  ] GET /health -> 200" in p.stdout  # offline tests still ran


def test_other_env_lines_are_preserved(fake_copilot, tmp_path):
    (tmp_path / ".env").write_text("MAX_CONCURRENCY=2\n# note\n")
    run(tmp_path, fake_copilot.http_base, fake_copilot.ws_base)
    env = (tmp_path / ".env").read_text()
    assert "MAX_CONCURRENCY=2" in env and "# note" in env
