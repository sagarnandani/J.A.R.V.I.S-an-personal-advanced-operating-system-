#!/usr/bin/env bash
# What is actually wrong with this JARVIS.
#
# Run it on the machine JARVIS is running on. It prints one page you can
# paste anywhere, and it prints no secrets: no environment dump, no keys,
# no database URL, no tokens. Read it before pasting anyway.
#
#   bash scripts/doctor.sh            # assumes http://localhost:8080
#   bash scripts/doctor.sh http://localhost:8099
#
# It exists because "the buttons appear and nothing works" has three
# completely different causes that look identical from a browser: you are
# signed out, the container is older than the page, or self-development
# has no repository to write to. Guessing between them has cost days.
set -uo pipefail

BASE="${1:-http://localhost:8080}"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
val() { printf '  %-24s %s\n' "$1" "$2"; }

echo "JARVIS doctor — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "checking $BASE"

# --- 1. is anything listening -------------------------------------------
say "1. Is JARVIS answering?"
# Delete the previous run's copy first. Without this, a server that is
# down reads back the last healthy answer and the whole page below is a
# confident lie -- which is exactly what happened the first time this
# script was run against a dead port.
rm -f /tmp/_jh.json
CODE=$(curl -s -o /tmp/_jh.json -w '%{http_code}' --max-time 10 "$BASE/health" 2>/dev/null)
CURLED=$?
# `|| echo 000` was wrong here: curl already prints 000 through -w on a
# failed connection, so the fallback appended a second one and the
# equality test never matched.
if [ $CURLED -ne 0 ] || [ -z "${CODE//0/}" ] || [ ! -s /tmp/_jh.json ]; then
  val "reachable" "NO — nothing answered at $BASE (curl said ${CODE:-nothing})"
  echo
  echo "  Nothing else here can be checked. On this machine:"
  echo "    docker compose ps"
  echo "    docker compose logs --tail=60 jarvis"
  echo "  If the container is up, JARVIS may be on a different port —"
  echo "  check the 'ports:' line in docker-compose.yml and re-run:"
  echo "    bash scripts/doctor.sh http://localhost:<port>"
  exit 1
fi
val "reachable" "yes (HTTP $CODE)"

PY=$(command -v python3 || command -v python)
field() { "$PY" -c "
import json,sys
try: d=json.load(open('/tmp/_jh.json'))
except Exception: print('(unreadable)'); raise SystemExit
for part in '$1'.split('.'):
    d = (d or {}).get(part) if isinstance(d, dict) else None
print('-' if d is None else (json.dumps(d) if isinstance(d,(dict,list)) else d))
" 2>/dev/null; }

# --- 2. which build ------------------------------------------------------
say "2. Which build is running?"
val "commit" "$(field running.commit)"
val "branch" "$(field running.branch)"
val "how it knows" "$(field running.how)"
if command -v git >/dev/null && [ -d .git ]; then
  val "checkout here" "$(git rev-parse --short=12 HEAD 2>/dev/null) on $(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  echo
  echo "  If those two commits differ, the rebuild did not take. Force it:"
  echo "    docker compose build --no-cache jarvis && docker compose up -d"
fi

# --- 3. can it build itself ---------------------------------------------
say "3. Can JARVIS build changes to itself?"
val "usable" "$(field self_development.usable)"
val "repo path" "$(field self_development.repo_path)"
val "branch" "$(field self_development.branch)"
WHY=$(field self_development.why_not)
[ "$WHY" != "-" ] && { echo; echo "  why not: $WHY"; }
FIX=$(field self_development.fix)
[ "$FIX" != "-" ] && { echo "  fix:     $FIX"; }

# --- 4. sign-in ----------------------------------------------------------
say "4. Will the page be able to talk to it?"
DEV=$(field dev_mode)
val "dev_mode" "$DEV"
if [ "$DEV" = "True" ] || [ "$DEV" = "true" ]; then
  echo "  Authentication is BYPASSED. Fine on a private machine, and must"
  echo "  never be true on anything reachable from the internet."
else
  echo "  Sign-in is required, so every button needs a valid login cookie."
  echo "  If the page loads but nothing works, you are probably signed out."
  echo "  Over plain http the cookie is only kept when COOKIE_SECURE=false."
fi

# --- 5. schema -----------------------------------------------------------
say "5. Database"
val "migrations in image" "$(field schema.migrations_in_image)"
val "applied" "$(field schema.applied | tr -d '[]\"' | tr ',' ' ' | wc -w) migration(s)"
val "emergency stop" "$(field emergency_stop)"

# --- 6. protected core ---------------------------------------------------
say "6. Protected core"
val "constitution present" "$(field constitution.present)"
val "protected paths" "$(field constitution.protected_paths)"
val "fingerprint" "$(field constitution.digest)"

# --- 7. the log ----------------------------------------------------------
say "7. Recent errors in the log"
if command -v docker >/dev/null; then
  docker compose logs --tail=200 jarvis 2>/dev/null \
    | grep -iE "error|traceback|exception|refused|denied" | tail -12 \
    | sed 's/^/  /' || echo "  (could not read the log)"
  echo "  (grep of the last 200 lines; run 'docker compose logs -f jarvis' to watch)"
else
  echo "  docker not found — skipping"
fi

say "Done."
echo "  Paste everything above. It contains no keys, no passwords and no"
echo "  database URL. If you added anything to this script, check that too."
