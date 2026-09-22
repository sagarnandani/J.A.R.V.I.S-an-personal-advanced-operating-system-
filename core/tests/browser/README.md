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

```bash
node tests/browser/fits_the_screen.mjs        # six viewports, both layouts
node tests/browser/ios_viewport_height.mjs    # the iOS 100vh trap
node tests/browser/stale_script.mjs           # a deploy that half-arrives
node tests/browser/offer_in_chat.mjs          # ask in chat, work happens
node tests/browser/voice_offer.mjs            # ask aloud, work happens
node tests/browser/nav_and_layout.mjs         # every tab, three sizes
node tests/browser/money.mjs                  # the ledger, in and out
```

The Media tab needs two seeded pieces, because the mock provider cannot
write a script or a review:

```bash
DATABASE_URL=... python tests/browser/seed_media.py
node tests/browser/media_tab.mjs media.png    # scan → read → approve
```

The Agents page needs one agent left mid-task, because a check server
never catches a real one in flight:

```bash
DATABASE_URL=... python tests/browser/seed_busy_agent.py
node tests/browser/agents_page.mjs agents.png agents-phone.png
```

Which model is good at which job, which needs a measured track record
the check server can never accumulate on its own:

```bash
DATABASE_URL=... python tests/browser/seed_model_history.py
node tests/browser/which_model_is_good.mjs which-model.png
```

`which_model_is_good.mjs` seeds two models on one capability: one failing
with enough runs behind it to say so, and one failing *worse* on three
runs. The second is the point. It checks that the failing model is marked
as the one being routed around, that the three-run model is shown but
explicitly not judged, and that the model named on screen is the one the
router would actually escalate away from -- the panel had its own copy of
that judgement for an hour and the two disagreed.

`agents_page.mjs` fetches `/v1/org` itself and compares the page against
it, so a page that had drifted from the registry — an agent shown that
was retired, or one missing that was registered — fails here rather than
being discovered months later. It also checks that JARVIS and a
coordinator are presented as what they are rather than as agents, that
unmeasured figures are named as unmeasured, and that there is no
create-agent button.

Asking JARVIS in chat to write something, which is the bug the owner
reported:

```bash
node tests/browser/make_in_chat.mjs make.png
```

`make_in_chat.mjs` injects only the marked reply, because the mock
provider cannot decide to mark a message. Everything after that is real:
the card, the endpoint, the Director, the content record and the Media
tab the link opens. On a keyless check server the chain fails at its first
step, which is the correct outcome and still exercises the wiring — what a
finished piece looks like is `media_tab.mjs`'s job, from seeded data.

A brief becoming a branch. The model calls that plan and write code
cannot run on a check server, so the two states that matter are seeded:

```bash
DATABASE_URL=... python tests/browser/seed_change_request.py
node tests/browser/build_from_a_brief.mjs build.png
```

`build_from_a_brief.mjs` checks that a proposal shows its plan, the parts
it said it could NOT do, the diff and the test result; that failing tests
are shown as failing rather than dressed up; that a plan can be written
but not approved, and a proposal the reverse; that the diff scrolls
inside its own box rather than pushing the page sideways; and that the
page never offers to merge or push.

Handing JARVIS a document, which needs no seeding:

```bash
node tests/browser/attach_a_document.mjs attach.png
```

`attach_a_document.mjs` attaches a build brief with a prompt-injection
attempt inside it. It checks the upload, that reading a file starts
nothing and says so, that the message carries the attachment's id rather
than the document, that the document is stored exactly as written, that
sending clears the chip so nothing goes twice, and that a file JARVIS
cannot read says why.

What is running right now, on the home screen. A check server finishes
everything instantly, so the one state that mattered — something running,
and running too long — has to be seeded:

```bash
DATABASE_URL=... python tests/browser/seed_stalled_work.py
node tests/browser/right_now.mjs now.png
```

`right_now.mjs` checks the thing that could not be answered for a week:
that work in flight is visible without leaving the conversation, that
each row names the agent and how long it has been going, that work past
five minutes is drawn as stuck rather than as working, and that the panel
disappears entirely when nothing is running — a panel that says "nothing"
all day is one you stop reading.

The spoken reply path has its own check, which needs no seeding:

```bash
node tests/browser/voice_profile.mjs voice.png
```

Chromium in a sandbox has no audio device and no system voices, so
`voice_profile.mjs` cannot check what JARVIS sounds like. It replaces
`window.speechSynthesis` with a recorder and checks everything that made
the old version wrong: that the page asks the server for the voice rather
than deciding for itself, that a long reply is queued as sentences rather
than truncated at 800 characters, that turning voice off empties the queue
instead of only cancelling the sentence in flight, and that a reply in
another script is read by a voice that can read it.

`media_tab.mjs` fakes only the scan's *answer*, at the network boundary,
since a real scout needs an API key this server does not have. Everything
else is real: the ranked queue, the piece list, the script with its
citations, both cost figures, and the approve button — including that a
piece already decided on offers no way to decide again, and that a piece
the strategist declined is not shown as a failure.

`offer_in_chat.mjs` injects a marked reply at the network boundary,
because the mock provider cannot decide to mark a message. Everything
downstream of that is real: the strip, the registry check, the planner,
the agents, the workflow, and the memory it leaves behind.

`stale_script.mjs` checks that static files are served with a
Cache-Control that makes a browser ask before reusing them, and that a
nav button for a view the script does not know stays un-highlighted and
says so. Both come from a real failure: an iPad ran new HTML against an
`app.js` cached from before the Tasks tab existed, so the button was
there, the code was not, and tapping it highlighted the button and did
nothing at all.

`ios_viewport_height.mjs` deserves a note. On iOS Safari `100vh` is the
*large* viewport — the height the page would have if the toolbars were
hidden — so anything sized `calc(100vh - x)` is laid out against a number
bigger than what you can see, and its bottom edge is unreachable. Chromium
has no such gap, so it cannot show this by itself. The script renders at
the height iOS *reports* and asserts everything fits inside the height iOS
actually *shows*. That is what makes it able to fail.

That bug shipped once: the Tasks panel worked on a phone and put 50px of
the results list beyond the edge of an iPad, because the rule only applies
from 900px up and a phone never reaches it.

Third-party requests (Google Fonts, the sign-in script) are reported as
blocked in a sandbox with no egress. That is the sandbox, not JARVIS —
the scripts fail only on requests to JARVIS itself.
