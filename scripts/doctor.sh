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
#
# The most valuable section, and the one that has to interpret itself.
# Printing "-" and leaving the reader to notice what is missing is how
# the first version of this script sent a whole page of stale-but-
# plausible numbers back and taught nobody anything.
say "2. Which build is running?"
RUNNING=$(field running.commit)
HAS_NEW_HEALTH=$(field running.how)
val "commit" "$RUNNING"
val "branch" "$(field running.branch)"
val "how it knows" "$HAS_NEW_HEALTH"

LOCAL=""
if command -v git >/dev/null && [ -d .git ]; then
  LOCAL=$(git rev-parse --short=12 HEAD 2>/dev/null)
  val "checkout here" "$LOCAL on $(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
fi

STALE=""
if [ "$HAS_NEW_HEALTH" = "-" ]; then
  STALE="yes"
  echo
  echo "  >> THE CONTAINER IS OLDER THAN YOUR CHECKOUT."
  echo
  echo "  /health did not report which build it is running. That field has"
  echo "  existed since the commit that added this script, so the code"
  echo "  answering is older than the code you have checked out."
  echo
  echo "  Note what this does NOT look like: your Constitution fingerprint"
  echo "  below will still be current, because CONSTITUTION.md is mounted"
  echo "  from your checkout. Only the Python is baked into the image, and"
  echo "  only the Python is stale. That is why a rebuild can look like it"
  echo "  worked."
elif [ -n "$LOCAL" ] && [ "$RUNNING" != "-" ] && [ "$RUNNING" != "$LOCAL" ]; then
  STALE="yes"
  echo
  echo "  >> THE CONTAINER IS RUNNING $RUNNING, YOUR CHECKOUT IS $LOCAL."
fi

if [ -n "$STALE" ]; then
  echo
  echo "  Rebuild and force the container to be replaced:"
  echo "    docker compose build --no-cache jarvis"
  echo "    docker compose up -d --force-recreate"
  echo
  echo "  'docker compose up -d' on its own does not rebuild, and"
  echo "  'up -d --build' will reuse the old container if it thinks"
  echo "  nothing changed. Then run this script again."
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
if ! command -v docker >/dev/null; then
  echo "  docker not found — skipping"
else
  LOG=$(docker compose logs --tail=200 jarvis 2>/dev/null) \
    || LOG=$(docker compose logs --tail=200 2>/dev/null) \
    || LOG=""
  if [ -z "$LOG" ]; then
    echo "  Could not read the log. Usually one of:"
    echo "    - you are not in the folder with docker-compose.yml"
    echo "    - the service is not called 'jarvis' (docker compose ps)"
    echo "    - your user is not in the docker group (try with sudo)"
  else
    FOUND=$(printf '%s\n' "$LOG" \
      | grep -iE "error|traceback|exception|refused|denied" | tail -12)
    if [ -z "$FOUND" ]; then
      echo "  No errors in the last 200 lines."
    else
      printf '%s\n' "$FOUND" | sed 's/^/  /'
    fi
  fi
  echo "  (run 'docker compose logs -f jarvis' to watch it live)"
fi

# --- 8. the verdict ------------------------------------------------------
say "8. Most likely problem"
if [ -n "${STALE:-}" ]; then
  echo "  The running container is older than your checkout. Everything"
  echo "  else here is describing code you have already replaced, so fix"
  echo "  that first and run this again before reading anything else."
elif [ "$(field self_development.usable)" = "False" ]; then
  echo "  Self-development cannot run: $(field self_development.why_not)"
  echo "  $(field self_development.fix)"
elif [ "$DEV" = "False" ] || [ "$DEV" = "false" ]; then
  echo "  Nothing structural is wrong. If buttons still do nothing, you are"
  echo "  most likely signed out — the page will now show a red banner"
  echo "  saying so. If there is no banner, your browser is running a"
  echo "  cached app.js: hard-reload, or clear website data on iOS."
else
  echo "  Nothing obviously wrong. Open the page and read the red banner if"
  echo "  one appears; it names the failing call and the status."
fi

say "Done."
echo "  Paste everything above. It contains no keys, no passwords and no"
echo "  database URL. If you added anything to this script, check that too."
