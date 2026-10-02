#!/usr/bin/env bash
# Pull the latest code from GitHub, install requirements and restart the app (cPanel / Passenger).
#
#   ./update.sh                       update from the default branch below
#   ./update.sh -b other-branch       update from another branch
#   ./update.sh --no-check            skip the public URL check at the end
#
# First run, when the folder isn't a git checkout yet (also works later):
#   curl -fsSL https://raw.githubusercontent.com/dezhcode/Web-Service/BRANCH/update.sh | bash
#
# .env and other untracked files are kept. Local edits to files from the repo are overwritten
# (a patch with them is saved as .update-backup-*.patch first). If the new code fails to import,
# the previous version is restored and the app is not restarted.
#
# Output is plain ASCII on purpose: the cPanel terminal mangles right-to-left text.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/dezhcode/Web-Service.git}"
BRANCH="${BRANCH:-ccr-ab4c9b4d-b7wg94}"
APP_DIR="${APP_DIR:-/home/wmkmbrcs/Api}"
VENV="${VENV:-/home/wmkmbrcs/virtualenv/Api/3.12/bin/activate}"
SITE_URL="${SITE_URL:-https://dezhcode.pyho.ir}"
check_url=1

while [ $# -gt 0 ]; do
  case "$1" in
    -b|--branch)  BRANCH="$2"; shift 2 ;;
    -u|--url)     SITE_URL="$2"; shift 2 ;;
    --no-check)   check_url=0; shift ;;
    -h|--help)    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\n== %s ==\n' "$*"; }
ok()   { printf '[OK  ] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
die()  { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

# Run from the script's folder when it is inside the app, else from APP_DIR (curl | bash).
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/passenger_wsgi.py" ]; then APP_DIR="$here"; fi
cd "$APP_DIR" || die "app folder not found: $APP_DIR (set APP_DIR=...)"

if [ -z "${VIRTUAL_ENV:-}" ] && [ -f "$VENV" ]; then
  # shellcheck disable=SC1090
  source "$VENV"
fi
PY="$(command -v python3 || command -v python)" || die "python not found; activate the virtualenv first"

say "Update $APP_DIR from $BRANCH"
command -v git >/dev/null || die "git is not installed on this host"

if [ ! -d .git ]; then
  git init -q
  git remote add origin "$REPO_URL"
  ok "turned the folder into a git checkout (existing files are kept)"
fi
git remote set-url origin "$REPO_URL"
prev="$(git rev-parse -q --verify HEAD || true)"

git fetch -q --depth 1 origin "$BRANCH" || die "could not download branch '$BRANCH' from $REPO_URL"
new="$(git rev-parse FETCH_HEAD)"

if [ -n "$prev" ] && [ "$prev" = "$new" ] && git diff --quiet HEAD; then
  ok "already up to date ($(git log -1 --format='%h %s' HEAD))"
else
  if [ -n "$prev" ] && ! git diff --quiet HEAD; then
    backup=".update-backup-$(date +%Y%m%d-%H%M%S).patch"
    git diff HEAD > "$backup"
    warn "local edits overwritten; saved to $backup"
  fi
  git reset -q --hard "$new"
  git checkout -q -B "$BRANCH" "$new"
  ok "code updated to $(git log -1 --format='%h %s' HEAD)"
fi

say "Requirements"
"$PY" -m pip install -q --disable-pip-version-check -r requirements.txt \
  && ok "pip install -r requirements.txt" \
  || warn "pip install failed; continuing with the installed packages"

say "Import check"
if err="$("$PY" -c 'import passenger_wsgi' 2>&1)"; then
  ok "passenger_wsgi imports"
else
  printf '%s\n' "$err" | tail -5
  if [ -n "$prev" ]; then
    git reset -q --hard "$prev"
    die "new code does not start; restored previous version $(git log -1 --format=%h HEAD), app NOT restarted"
  fi
  die "new code does not start; app NOT restarted"
fi

say "Restart"
mkdir -p tmp
touch tmp/restart.txt
ok "touched tmp/restart.txt (Passenger reloads on the next request)"

[ "$check_url" = 1 ] || exit 0
command -v curl >/dev/null || { warn "curl not found; skipping URL check"; exit 0; }

say "Check $SITE_URL"
check() {  # check <path> <expected text>
  local body
  for _ in 1 2 3 4 5 6; do
    if body="$(curl -fsS --max-time 20 "$SITE_URL$1" 2>/dev/null)" && [[ "$body" == *"$2"* ]]; then
      ok "GET $1"; return 0
    fi
    sleep 3
  done
  warn "GET $SITE_URL$1 did not answer as expected; see stderr.log in $APP_DIR"
  return 1
}
fails=0
check /health '"ok"' || fails=1
check /doc 'playground' || fails=1
[ "$fails" = 0 ] && printf '\nDone. Docs: %s/doc\n' "$SITE_URL"
exit "$fails"
