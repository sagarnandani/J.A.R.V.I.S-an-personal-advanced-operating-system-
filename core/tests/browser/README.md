# Browser checks

`pytest` never opens a browser, and this dashboard's failures have almost
all been ones only a browser can see: a panel 40px below the fold, a page
that scrolls sideways on a phone, a CSS class that matched nothing. These
scripts drive the real page in real Chromium at real viewport sizes.

They are not part of `pytest` because they need a running server and a
browser, and a test that cannot run in CI should not be able to fail the
suite. Run them by hand when you change the dashboard.

## Running them

Start a server against a scratch database — never your own:

```bash
createdb -O jarvis jarvis_panel     # once
cd core
DATABASE_URL="postgres://jarvis:jarvis@localhost:5432/jarvis_panel" \
  SESSION_SECRET=panel-test-secret DEV_MODE=true LLM_PROVIDER=mock \
  OWNER_EMAIL=you@example.com \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8099
```

`DEV_MODE=true` skips sign-in and `LLM_PROVIDER=mock` means no API key and
no spend. Both are safe here and nowhere else.

Then:

```bash
node tests/browser/tasks_panel.mjs out.png          # plan → run → history
DATABASE_URL=... python tests/browser/seed_factcheck.py   # a finished check
node tests/browser/verdicts.mjs v.png v-phone.png   # verdicts, 3 viewports
```

Each prints one `ok` line per check and exits non-zero on the first
failure. `tasks_panel.mjs` exercises the whole flow against the mock
provider; `verdicts.mjs` needs the seeded workflow, because the mock
cannot produce a fact-check result and that is the richest thing the panel
renders.

Third-party requests (Google Fonts, the sign-in script) are reported as
blocked in a sandbox with no egress. That is the sandbox, not JARVIS —
the scripts fail only on requests to JARVIS itself.
