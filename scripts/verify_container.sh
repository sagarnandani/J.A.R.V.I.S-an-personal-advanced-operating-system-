#!/usr/bin/env bash
# Does this thing actually build and run in a container?
#
# Two items in docs/SELF_DEVELOPMENT.md have sat unticked for months with
# the same honest reason: `docker build` has never run against this
# repository, because the environment the code was written in has no
# Docker daemon. This is the script that closes them, run on YOUR
# machine, which does.
#
#   bash scripts/verify_container.sh                  # build and check
#   bash scripts/verify_container.sh --with-browser   # include Chromium
#   bash scripts/verify_container.sh --keep           # leave it running
#
# It builds the image, starts it against a throwaway Postgres, waits for
# it to come up, and then checks the things that are claimed about the
# container rather than the things that are easy to check:
#
#   * it builds at all;
#   * it does NOT run as root;
#   * /app is not writable by the user it runs as, so JARVIS cannot
#     rewrite its own source even from inside;
#   * it applies its own migrations and answers /health;
#   * it still works with a READ-ONLY root filesystem, which is the
#     second unticked item.
#
# Nothing here touches your real JARVIS. It uses its own container names,
# its own database and its own port, and removes them at the end.
set -uo pipefail

WITH_BROWSER=false
KEEP=false
for arg in "$@"; do
  case "$arg" in
    --with-browser) WITH_BROWSER=true ;;
    --keep) KEEP=true ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

IMAGE="jarvis-verify:$(date +%s)"
NET="jarvis-verify-net"
DB="jarvis-verify-db"
APP="jarvis-verify-app"
PORT=8791
PASSED=0
FAILED=0

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here" || exit 1

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
pass() { printf '  \033[32mok\033[0m    %s\n' "$*"; PASSED=$((PASSED + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILED=$((FAILED + 1)); }
note() { printf '        %s\n' "$*"; }

# shellcheck disable=SC2329  # invoked by the trap below
cleanup() {
  if [ "$KEEP" = true ]; then
    bold "Left running (--keep)"
    note "JARVIS:   http://localhost:$PORT"
    note "Stop it:  docker rm -f $APP $DB && docker network rm $NET"
    return
  fi
  docker rm -f "$APP" "$DB" >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
  docker rmi "$IMAGE" >/dev/null 2>&1
}
trap cleanup EXIT

# --- before anything else ---------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "There is no docker on this machine. That is the whole point of"
  echo "this script, so run it somewhere there is one."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but its daemon is not running (or this user"
  echo "cannot reach it). Start Docker, or add yourself to the docker"
  echo "group, and try again."
  exit 1
fi

# --- 1. does it build -------------------------------------------------------
bold "Building the image"
note "This takes a few minutes the first time."
BUILD_ARGS=()
if [ "$WITH_BROWSER" = true ]; then
  BUILD_ARGS=(--build-arg WITH_BROWSER=true)
  note "Including Chromium, which adds ~400MB and several minutes."
fi
if docker build "${BUILD_ARGS[@]}" -f infra/Dockerfile -t "$IMAGE" . > /tmp/jarvis-build.log 2>&1; then
  pass "docker build succeeded"
  note "Size: $(docker image inspect "$IMAGE" --format '{{.Size}}' | awk '{printf "%.0fMB", $1/1048576}')"
else
  fail "docker build failed"
  note "The last 20 lines:"
  tail -20 /tmp/jarvis-build.log | sed 's/^/        /'
  echo
  echo "Nothing else can be checked until it builds. Full log:"
  echo "  /tmp/jarvis-build.log"
  exit 1
fi

# --- 2. what does it run as -------------------------------------------------
bold "Who it runs as"
WHO="$(docker run --rm --entrypoint id "$IMAGE" -un 2>/dev/null)"
UID_IS="$(docker run --rm --entrypoint id "$IMAGE" -u 2>/dev/null)"
if [ "$UID_IS" = "0" ]; then
  fail "it runs as root inside the container"
  note "Root in a container is not root on your server, but it is one"
  note "container escape away from it."
else
  pass "runs as '$WHO' (uid $UID_IS), not root"
fi

# The boundary the Constitution draws in code, drawn a second time by the
# filesystem: JARVIS can read its own source and cannot write it.
if docker run --rm --entrypoint sh "$IMAGE" -c 'touch /app/app/__probe 2>/dev/null' ; then
  fail "the user it runs as can WRITE /app -- it can rewrite its own code"
  docker run --rm --entrypoint sh "$IMAGE" -c 'rm -f /app/app/__probe' >/dev/null 2>&1
else
  pass "/app is not writable by the user it runs as"
fi
if docker run --rm --entrypoint sh "$IMAGE" -c 'test -r /app/app/main.py'; then
  pass "and it can still read its own source"
else
  fail "it cannot read its own source, which will not start"
fi

# --- 3. does it actually come up -------------------------------------------
bold "Starting it against a throwaway database"
docker network create "$NET" >/dev/null 2>&1
docker rm -f "$DB" "$APP" >/dev/null 2>&1
docker run -d --name "$DB" --network "$NET" \
  -e POSTGRES_PASSWORD=verify -e POSTGRES_USER=jarvis -e POSTGRES_DB=jarvis \
  postgres:16-alpine >/dev/null 2>&1 || { fail "could not start Postgres"; exit 1; }

printf '        waiting for the database'
for _ in $(seq 1 60); do
  docker exec "$DB" pg_isready -U jarvis >/dev/null 2>&1 && break
  printf '.'; sleep 1
done
echo

start_app() {
  docker rm -f "$APP" >/dev/null 2>&1
  # shellcheck disable=SC2086
  docker run -d --name "$APP" --network "$NET" -p "$PORT:8080" \
    --security-opt no-new-privileges \
    $1 \
    -e DATABASE_URL="postgres://jarvis:verify@$DB:5432/jarvis" \
    -e SESSION_SECRET=verify-only-not-a-real-secret \
    -e DEV_MODE=true -e LLM_PROVIDER=mock -e COOKIE_SECURE=false \
    "$IMAGE" >/dev/null 2>&1
}

wait_for_health() {
  for _ in $(seq 1 45); do
    if curl -sf -o /dev/null "http://localhost:$PORT/health"; then return 0; fi
    sleep 1
  done
  return 1
}

start_app ""
if wait_for_health; then
  pass "it starts and answers /health"
  MIGRATED="$(curl -s "http://localhost:$PORT/health" | grep -o '"migrations"[^,}]*' || true)"
  [ -n "$MIGRATED" ] && note "$MIGRATED"
else
  fail "it did not come up"
  note "The last 25 lines of its log:"
  docker logs "$APP" 2>&1 | tail -25 | sed 's/^/        /'
fi

# Its own migrations, applied by itself. Without this, every schema
# change needs a human with a SQL editor.
TABLES="$(docker exec "$DB" psql -U jarvis -d jarvis -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null)"
if [ "${TABLES:-0}" -gt 5 ]; then
  pass "it applied its own migrations ($TABLES tables)"
else
  fail "the schema was not applied (found ${TABLES:-0} tables)"
fi

# --- 4. the second unticked item -------------------------------------------
bold "With a read-only root filesystem"
note "The remaining item in docs/SELF_DEVELOPMENT.md."
start_app "--read-only --tmpfs /tmp:rw,size=256m"
if wait_for_health; then
  pass "it also runs read-only"
  note "Add to docker-compose.yml under the core service:"
  note "    read_only: true"
  note "    tmpfs: [\"/tmp:size=256m\"]"
else
  fail "it does not start with a read-only filesystem"
  note "What it tried to write, from its log:"
  docker logs "$APP" 2>&1 | grep -iE "read-only|permission denied|errno 30" \
    | head -5 | sed 's/^/        /'
  note "That is worth knowing rather than guessing at -- it means"
  note "something writes to disk that nobody knew wrote to disk."
fi

# --- 5. the browser, if it was asked for ------------------------------------
if [ "$WITH_BROWSER" = true ]; then
  bold "The browser"
  if docker run --rm --entrypoint sh "$IMAGE" -c \
      'python -c "from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_content(\"<h1>ok</h1>\")
    assert pg.inner_text(\"h1\") == \"ok\"
    b.close()
print(\"launched\")"' >/dev/null 2>&1; then
    pass "Chromium is installed and launches"
  else
    fail "Chromium is in the image but will not launch"
    note "JARVIS still runs; it will say it has no browser."
  fi
fi

# --- what it all means ------------------------------------------------------
bold "Result"
printf '  %d passed, %d failed\n\n' "$PASSED" "$FAILED"
if [ "$FAILED" -eq 0 ]; then
  echo "  Both items in docs/SELF_DEVELOPMENT.md are now honestly ticked."
  echo "  Paste this output there, or just tell Claude it passed."
else
  echo "  Paste this whole output to Claude. Every failure above says"
  echo "  what was expected, which is most of the way to fixing it."
fi
exit "$FAILED"
