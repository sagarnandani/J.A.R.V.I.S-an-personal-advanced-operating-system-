# JARVIS Core (Stage 0)

A FastAPI service. One request in, one LLM call, one reply out, with the
exchange recorded to Postgres along the way.

## Layout

```
app/
  main.py          FastAPI app, wiring, startup/shutdown
  config.py        All configuration (env vars), one place
  db.py            Postgres connection pool
  auth.py          Firebase ID token verification (or dev-mode bypass)
  memory.py        Provenance-tagged memory writes/reads
  audit.py         Audit log writes/reads
  budget.py        Cost estimation + monthly spend tracking
  system_control.py  Emergency Stop flag
  llm/             Provider adapters behind a common interface
    base.py          the interface
    claude_adapter.py  real Claude calls
    mock_adapter.py    honest fallback when no API key is set
  routes/          One file per route group (health, message, budget, admin, records)
static/            The Stage 0 test console (see static/README.md)
tests/             pytest suite -- see below
```

## Why FastAPI / Python

Chosen for Stage 0 because: the owner reviews everything in plain
language rather than code, and FastAPI gives a free, auto-generated
interactive API explorer at `/docs` on any running deployment — useful for
poking at endpoints without needing a client. Python also has first-class
SDKs for both Anthropic and Firebase, which are the two external services
Stage 0 talks to.

## Running locally

See the root `README.md` for the docker-compose path (recommended — no
local Python setup needed). To run directly instead:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgres://jarvis:jarvis@localhost:5432/jarvis
export DEV_MODE=true
python3 -m uvicorn app.main:app --reload --port 8080
```

## Tests

```bash
. .venv/bin/activate
DATABASE_URL=postgres://jarvis:jarvis@localhost:5432/jarvis PYTHONPATH=. python3 -m pytest -v
```

Pure logic tests (budget math, memory validation) run with no database at
all. The two integration tests in `test_integration_loop.py` need a real
reachable Postgres with the schema applied (`db/migrate.py`) — they skip
themselves cleanly rather than failing if none is found.
