#!/usr/bin/env bash
# Ask the Copilot web service a question from the terminal.
#
#   ./ask.sh "your question"                 print the reply
#   ./ask.sh -s "your question"              stream the reply as it arrives
#   ./ask.sh -i https://site/pic.png "what is this?"     ask about an image
#   ./ask.sh -u https://other.example "question"         use another server
#
# API key: $API_KEY, or API_KEY= from the .env next to this script.
# Server:  -u, or $ASK_URL, or the default below (falls back from https to http if https is unreachable).

DEFAULT_URL="https://dezhcode.pyho.ir"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base="${ASK_URL:-$DEFAULT_URL}"
stream=0
image=""

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    -s|--stream) stream=1; shift ;;
    -i|--image)  image="$2"; shift 2 ;;
    -u|--url)    base="$2"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    --)          shift; break ;;
    -*)          echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)           break ;;
  esac
done
prompt="$*"
if [ -z "$prompt" ] && [ -z "$image" ]; then usage >&2; exit 2; fi

py="$(command -v python3 || command -v python)"
if [ -z "$py" ] || ! command -v curl >/dev/null; then
  echo "ask.sh needs curl and python3" >&2; exit 1
fi

key="${API_KEY:-}"
if [ -z "$key" ] && [ -f "$here/.env" ]; then
  key="$(grep -E '^API_KEY=' "$here/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"'\r ')"
fi
if [ -z "$key" ]; then
  echo "no API key: set API_KEY or run python setup_and_test.py to create .env" >&2; exit 1
fi

body="$("$py" -c '
import json, sys
d = {}
if sys.argv[1]: d["prompt"] = sys.argv[1]
if sys.argv[2]: d["image_url"] = sys.argv[2]
print(json.dumps(d))' "$prompt" "$image")"

# prints reply text (or error) from a JSON response body on stdin
parse_json='
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except ValueError:
    sys.stderr.write("unexpected response: " + raw[:300] + "\n"); sys.exit(1)
if "text" in d:
    print(d["text"])
else:
    sys.stderr.write("error: " + str(d.get("error", d)) + "\n"); sys.exit(1)'

# prints deltas as they arrive; SSE lines on stdin
parse_sse='
import json, sys
ok = False
other = []
for line in sys.stdin:
    if not line.startswith("data:"):
        other.append(line)
        continue
    ev = json.loads(line[5:])
    if ev["type"] == "delta":
        sys.stdout.write(ev["text"]); sys.stdout.flush()
    elif ev["type"] == "done":
        ok = True
    elif ev["type"] == "error":
        sys.stderr.write("\nerror: " + ev.get("message", "") + "\n"); sys.exit(1)
if not ok:
    msg = "".join(other).strip()
    try:
        msg = json.loads(msg).get("error", msg)
    except ValueError:
        pass
    sys.stderr.write("\nerror: " + (msg or "stream ended without a reply") + "\n")
    sys.exit(1)
print()'

request() {  # $1=base ; sets $code, writes body to $tmp
  curl -sS -o "$tmp" -w '%{http_code}' --max-time 150 -X POST "$1/api/chat" \
    -H "Authorization: Bearer $key" -H "Content-Type: application/json" --data-binary "$body" 2>/dev/null
}

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT

if [ "$stream" = 1 ]; then
  curl -sSN --max-time 150 -X POST "$base/api/chat/stream" \
    -H "Authorization: Bearer $key" -H "Content-Type: application/json" --data-binary "$body" \
    | "$py" -u -c "$parse_sse"
  rc=("${PIPESTATUS[@]}")
  if [ "${rc[0]}" != 0 ]; then echo "request failed (curl exit ${rc[0]}); check the URL and that the service is up" >&2; exit 1; fi
  exit "${rc[1]}"
fi

code="$(request "$base")"
if [ "$code" = "000" ] && [ "${base#https://}" != "$base" ]; then
  echo "https unreachable, retrying over http (the API key is sent unencrypted)" >&2
  base="http://${base#https://}"
  code="$(request "$base")"
fi
if [ "$code" = "000" ]; then echo "cannot reach $base" >&2; exit 1; fi
"$py" -c "$parse_json" < "$tmp"
