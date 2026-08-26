# Stage 0 test console

`index.html` is **not** the JARVIS client app. The architecture doc is
explicit that clients are their own future workstream ("voice/UI is the
owner's domain, not built here" -- Stage 0 Build Brief, section 4).

It exists only to satisfy two Stage 0 Definition-of-Done items that
otherwise have no way to be checked from an iPad browser:

- "Firebase Auth wired -- owner can log in from a browser"
- "reachable via HTTPS" / testable end-to-end from the owner's own device

It's a single static HTML file: a sign-in button (Firebase), a text box
that calls `POST /v1/message`, and buttons to view recent memories, the
audit log, and budget status. No framework, no build step, so there's
nothing here to break or to maintain as its own project. Once a real
client exists (later stage), this file can simply be deleted.
