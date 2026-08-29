# The JARVIS front end

Two pages, both plain HTML/JS served by the API itself. No build step, no
framework, no npm — open the file and read it.

## `index.html` + `app.js` — the dashboard

What you actually use. A heads-up display: conversation, what JARVIS
remembers, today's activity, spend, and the emergency stop.

**Every number on it is measured.** They all come from one request to
`GET /v1/dashboard`, which counts real rows. Where JARVIS cannot yet know
something — scheduled tasks, calendar events, project progress — the
panel says so rather than showing a plausible figure. A dashboard you
cannot trust is worse than no dashboard, and a fake number is worse than
a blank space.

One request rather than one per panel: against a hosted database, six
panels would mean six round trips before anything appeared.

The reactor turns while JARVIS is idle and speeds up while it is
thinking, so waiting looks like waiting. It greys out and stops when the
emergency stop is on. The waveform only moves when something is actually
happening — a waveform that dances at rest is decoration pretending to be
information.

Voice uses the browser's own speech engine: no API, no key, no cost. It
is off by default (Settings → Speak replies out loud).

## `console.html` — diagnostics

The original test console, kept because it does things the dashboard
deliberately does not: raw JSON, the sign-in fault-finder, restoring
forgotten memories, and bulk erase. Linked from Settings → Diagnostics.
