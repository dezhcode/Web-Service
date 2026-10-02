#!/usr/bin/env python3
"""One-shot installer + self-test for the Copilot web service (stdlib only to start).

    python setup_and_test.py                 # install, create .env + API key, run all tests
    python setup_and_test.py --url https://api.example.com
    python setup_and_test.py --rotate        # replace the existing API key with a new one

What it does
  1. checks Python, installs requirements.txt
  2. creates .env with a random API_KEY (an existing key is kept unless --rotate)
  3. imports passenger_wsgi and runs offline tests in-process (health, auth, validation, SSRF guard)
  4. checks that this host can reach Copilot and, if so, does a real chat + stream in-process
  5. touches tmp/restart.txt so Passenger reloads the app
  6. tests the public URL over HTTP (health, auth, chat, SSE) and prints the API key

Output is plain ASCII on purpose: cPanel's terminal garbles mixed RTL/LTR text.
Exit code is 1 if any check FAILed.
"""

import argparse
import io
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from wsgiref.util import setup_testing_defaults

HERE = Path(__file__).resolve().parent
DEFAULT_URL = "http://dezhcode.pyho.ir"
COPILOT_HTTP_BASE = os.environ.get("COPILOT_HTTP_BASE", "https://copilot.microsoft.com")
CHAT_TIMEOUT = 120

results = []  # (status, name)


def record(status, name, detail=""):
    results.append((status, name))
    line = "[%-4s] %s" % (status, name)
    if detail:
        line += " - " + detail
    print(line, flush=True)


def section(title):
    print("\n== %s ==" % title, flush=True)


def fatal(msg):
    record("FAIL", msg)
    summary()
    sys.exit(1)


# ───────────────────────────── 1. environment + install ──────────────────────

def step_environment():
    section("Environment")
    v = sys.version_info
    if v < (3, 9):
        fatal("Python %d.%d is too old (need 3.9+)" % (v.major, v.minor))
    record("OK", "Python %d.%d.%d" % v[:3], sys.executable)
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        record("WARN", "not inside a virtualenv",
               "run: source /home/USER/virtualenv/APP/3.x/bin/activate first")


def step_install():
    section("Dependencies")
    req = HERE / "requirements.txt"
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
    )
    if proc.returncode != 0:
        print(proc.stdout[-1500:])
        fatal("pip install -r requirements.txt failed")
    record("OK", "pip install -r requirements.txt")


def step_check_imports():
    missing = []
    for mod in ("requests", "starlette", "a2wsgi", "websockets"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        fatal("missing modules: %s (run without --no-install)" % ", ".join(missing))
    record("OK", "required modules import")


# ───────────────────────────── 2. .env + API key ─────────────────────────────

def step_env_file(app_dir, rotate):
    section("API key and .env")
    app_dir.mkdir(parents=True, exist_ok=True)
    env_path = app_dir / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

    def get(name):
        for line in lines:
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("\"'")
        return ""

    key = get("API_KEY")
    if key and not rotate:
        record("OK", "existing API_KEY kept", "use --rotate to replace it")
    else:
        key = secrets.token_urlsafe(32)
        lines = [l for l in lines if not l.strip().startswith("API_KEY=")]
        lines.insert(0, "API_KEY=" + key)
        record("OK", "generated new API_KEY" + (" (rotated)" if rotate else ""))
    if not get("COPILOT_MODE"):
        lines.append("COPILOT_MODE=smart")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(str(env_path), 0o600)
    except OSError:
        pass
    record("OK", "wrote %s (mode 600)" % env_path)
    return key


# ───────────────────────────── 3. in-process tests ───────────────────────────

def wsgi_call(application, method, path, body=None, key=None):
    payload = json.dumps(body).encode() if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(payload),
        "CONTENT_LENGTH": str(len(payload)),
        "CONTENT_TYPE": "application/json",
    }
    if key:
        environ["HTTP_AUTHORIZATION"] = "Bearer " + key
    setup_testing_defaults(environ)
    out = {}

    def start_response(status, headers, exc_info=None):
        out["status"] = int(status.split()[0])
        out["headers"] = dict((k.lower(), v) for k, v in headers)

    out["body"] = b"".join(application(environ, start_response))
    return out["status"], out["headers"], out["body"]


def check(name, ok, detail=""):
    record("OK" if ok else "FAIL", name, "" if ok else detail)
    return ok


def step_import_app(key):
    section("Application import")
    os.environ["API_KEY"] = key
    sys.path.insert(0, str(HERE))
    try:
        import passenger_wsgi
    except Exception as e:  # noqa: BLE001
        fatal("cannot import passenger_wsgi: %s: %s" % (type(e).__name__, e))
    record("OK", "passenger_wsgi imports", "entry point: application")
    return passenger_wsgi.application


def step_local_tests(application, key):
    section("Offline tests (in-process, no Copilot needed)")
    s, _, b = wsgi_call(application, "GET", "/health")
    check("GET /health -> 200", s == 200 and b"ok" in b, "got %s %r" % (s, b[:80]))

    s, _, _ = wsgi_call(application, "POST", "/api/chat", {"prompt": "x"})
    check("request without key -> 401", s == 401, "got %s" % s)

    s, _, _ = wsgi_call(application, "POST", "/api/chat", {"prompt": "x"}, key="wrong-key")
    check("request with wrong key -> 401", s == 401, "got %s" % s)

    s, _, _ = wsgi_call(application, "POST", "/api/chat", {}, key=key)
    check("empty prompt -> 400", s == 400, "got %s" % s)

    s, _, _ = wsgi_call(application, "POST", "/api/chat",
                        {"image_url": "http://127.0.0.1/x.png"}, key=key)
    check("internal image_url blocked (SSRF) -> 400", s == 400, "got %s" % s)

    s, _, _ = wsgi_call(application, "GET", "/ws")
    check("GET /ws without WebSocket -> 426", s == 426, "got %s" % s)


def run_with_timeout(fn, seconds):
    box = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError("no answer within %ds" % seconds)
    if "error" in box:
        raise box["error"]
    return box["value"]


def step_copilot_probe():
    section("Copilot reachability from this host")
    import requests

    try:
        r = requests.post(
            COPILOT_HTTP_BASE.rstrip("/") + "/c/api/start",
            json={"timeZone": "Asia/Tehran", "startNewConversation": True, "teenSupportEnabled": False},
            headers={"User-Agent": "CopilotNative/30.0.430320002 (Android 9; samsung; SM-G988N)"},
            timeout=15,
        )
    except requests.RequestException as e:
        record("FAIL", "cannot connect to Copilot", "%s. Outbound access is blocked "
               "(host firewall, or host located where Copilot is unavailable)" % type(e).__name__)
        return False
    if r.status_code == 200 and "currentConversationId" in r.text:
        record("OK", "Copilot start-conversation endpoint reachable")
        return True
    record("FAIL", "Copilot answered HTTP %d" % r.status_code,
           "403 usually means this host's IP/region is blocked; try a host outside Iran")
    return False


def parse_sse(text):
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:]))
    return events


def step_local_chat(application, key):
    section("Real chat tests (in-process, talks to Copilot)")
    try:
        s, _, b = run_with_timeout(lambda: wsgi_call(
            application, "POST", "/api/chat", {"prompt": "Reply with exactly one word: pong"}, key=key),
            CHAT_TIMEOUT)
        text = json.loads(b).get("text", "") if s == 200 else ""
        check("POST /api/chat returns a reply", s == 200 and bool(text),
              "status %s: %s" % (s, b[:200]))
        if text:
            print("       reply: %s" % text[:120].replace("\n", " "))
    except Exception as e:  # noqa: BLE001
        record("FAIL", "POST /api/chat", "%s: %s" % (type(e).__name__, e))

    try:
        s, _, b = run_with_timeout(lambda: wsgi_call(
            application, "POST", "/api/chat/stream", {"prompt": "Count from 1 to 5"}, key=key),
            CHAT_TIMEOUT)
        events = parse_sse(b.decode("utf-8", "replace")) if s == 200 else []
        deltas = [e for e in events if e.get("type") == "delta"]
        ok = s == 200 and bool(deltas) and events[-1].get("type") == "done"
        check("POST /api/chat/stream streams deltas + done", ok,
              "status %s, events: %s" % (s, [e.get("type") for e in events][:6]))
    except Exception as e:  # noqa: BLE001
        record("FAIL", "POST /api/chat/stream", "%s: %s" % (type(e).__name__, e))


# ───────────────────────────── 4. restart ────────────────────────────────────

def step_restart(app_dir):
    section("Passenger restart")
    tmp = app_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "restart.txt").touch()
    record("OK", "touched %s" % (tmp / "restart.txt"), "Passenger reloads on the next request")


# ───────────────────────────── 5. public URL tests ───────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def http(method, url, body=None, key=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "User-Agent": "setup-and-test/1"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:  # noqa: BLE001
        return None, {}, ("%s: %s" % (type(e).__name__, e)).encode()


def step_public(url, key, copilot_ok):
    section("Public URL tests: %s" % url)
    base = url.rstrip("/")

    status = body = headers = None
    for attempt in range(6):  # Passenger may need a moment to boot after restart
        status, headers, body = http("GET", base + "/health", timeout=20)
        if status == 200:
            break
        time.sleep(2)
    if status in (301, 302, 307, 308):
        record("FAIL", "GET /health redirects (%s)" % status,
               "use the final URL instead: %s" % headers.get("Location"))
        return
    if status != 200 or b"ok" not in body:
        hint = ("check stderr.log in the app folder and Setup Python App settings "
                "(URL, startup file passenger_wsgi.py, entry point application)")
        record("FAIL", "GET /health -> %s" % status, "%s | %s" % (body[:120].decode("utf-8", "replace"), hint))
        return
    record("OK", "GET /health -> 200")

    s, _, b = http("POST", base + "/api/chat", {"prompt": "x"})
    if s == 503:
        record("FAIL", "auth is on", "server has no API_KEY: restart the app so .env is loaded")
        return
    check("request without key -> 401", s == 401, "got %s" % s)

    if not copilot_ok:
        record("SKIP", "public chat tests", "Copilot is not reachable from this host (see above)")
    else:
        s, _, b = http("POST", base + "/api/chat", {"prompt": "Reply with exactly one word: pong"},
                       key=key, timeout=CHAT_TIMEOUT)
        try:
            text = json.loads(b).get("text", "") if s == 200 else ""
        except ValueError:
            text = ""
        check("POST /api/chat returns a reply", s == 200 and bool(text),
              "status %s: %s" % (s, b[:200].decode("utf-8", "replace")))

        step_public_sse(base, key)

    record("SKIP", "WebSocket /ws", "not available on Passenger/shared hosting (use /api/chat/stream)")

    if base.startswith("http://"):
        https = "https://" + base[len("http://"):]
        s, _, _ = http("GET", https + "/health", timeout=15)
        if s == 200:
            record("WARN", "plain http in use", "https works too - use %s so the API key is encrypted" % https)
        else:
            record("WARN", "plain http in use",
                   "the API key travels unencrypted; enable AutoSSL (cPanel > SSL/TLS Status)")


def step_public_sse(base, key):
    req = urllib.request.Request(
        base + "/api/chat/stream",
        data=json.dumps({"prompt": "Count from 1 to 5, one number per line"}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    t0 = time.time()
    first = None
    events = []
    try:
        with _opener.open(req, timeout=CHAT_TIMEOUT) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:])
                events.append(ev)
                if first is None and ev.get("type") == "delta":
                    first = time.time() - t0
                if ev.get("type") in ("done", "error"):
                    break
    except Exception as e:  # noqa: BLE001
        record("FAIL", "POST /api/chat/stream", "%s: %s" % (type(e).__name__, e))
        return
    total = time.time() - t0
    deltas = sum(1 for e in events if e.get("type") == "delta")
    ok = deltas > 0 and events and events[-1].get("type") == "done"
    if not check("POST /api/chat/stream delivers deltas + done", ok,
                 "events: %s" % [e.get("type") for e in events][:6]):
        return
    if first is not None and deltas > 1 and total - first < 0.2:
        record("WARN", "streaming looks buffered",
               "all chunks arrived together; the host buffers responses - /api/chat is equivalent")
    else:
        record("OK", "streaming is live", "first chunk after %.1fs, finished after %.1fs" % (first or 0, total))


# ───────────────────────────── summary ───────────────────────────────────────

def summary():
    section("Summary")
    counts = {}
    for status, _ in results:
        counts[status] = counts.get(status, 0) + 1
    print("  " + "  ".join("%s: %d" % (k, counts[k]) for k in ("OK", "WARN", "SKIP", "FAIL") if k in counts))
    for status, name in results:
        if status == "FAIL":
            print("  failed: " + name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL, help="public URL to test (default: %(default)s)")
    ap.add_argument("--no-url", action="store_true", help="skip the public URL tests")
    ap.add_argument("--rotate", action="store_true", help="replace the existing API key")
    ap.add_argument("--no-install", action="store_true", help="skip pip install")
    ap.add_argument("--no-restart", action="store_true", help="do not touch tmp/restart.txt")
    ap.add_argument("--skip-live", action="store_true", help="skip tests that talk to Copilot")
    ap.add_argument("--app-dir", default=str(HERE), help="where .env and tmp/ live (default: this folder)")
    args = ap.parse_args()
    app_dir = Path(args.app_dir).resolve()

    step_environment()
    if not args.no_install:
        step_install()
    step_check_imports()
    key = step_env_file(app_dir, args.rotate)
    application = step_import_app(key)
    step_local_tests(application, key)

    copilot_ok = False
    if args.skip_live:
        record("SKIP", "Copilot tests", "--skip-live")
    else:
        copilot_ok = step_copilot_probe()
        if copilot_ok:
            step_local_chat(application, key)

    if not args.no_restart:
        step_restart(app_dir)
    if not args.no_url:
        step_public(args.url, key, copilot_ok and not args.skip_live)

    summary()
    section("Your API key (keep it secret)")
    print(key)
    base = args.url.rstrip("/")
    print("\nExample:\n  curl -X POST %s/api/chat -H 'Authorization: Bearer %s' "
          "-H 'Content-Type: application/json' -d '{\"prompt\":\"hello\"}'" % (base, key))
    sys.exit(1 if any(s == "FAIL" for s, _ in results) else 0)


if __name__ == "__main__":
    main()
