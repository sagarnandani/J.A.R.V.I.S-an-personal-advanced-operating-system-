# Governed self-development — what is actually built

Status report against the master brief. Written to be read by Sagar, not
by a compiler. Everything marked **built** has been run against a live
server on this machine, not only against the test suite — twice this
month something passed its tests and did not work when it ran, so
"tests pass" is no longer treated as evidence on its own.

Last verified: 12 September 2026. 697 tests passing.

---

## The short version

JARVIS can now read a brief, write a plan, open its own git branch, edit
files there, run the tests, and show you the result. It cannot touch what
is running, and it cannot touch the parts of itself that decide what it
is allowed to do.

Nothing reaches the live system without you pressing approve.

---

## What is built

### 1. A branch is the only place it can write

When JARVIS builds a change it does not edit the files that are running.
It opens a **git worktree** — a second, separate copy of the repository on
a new branch — and works only inside that. When it finishes, the worktree
is closed and removed.

The code that does this (`core/app/dev/repo.py`) contains no `push`, no
`merge`, no `rebase`, no `reset` and no `checkout`. That is not a policy
written in a comment; a test greps the file for those words and fails the
build if one appears. JARVIS cannot move its work onto the running system
even if it decides it should.

It also refuses to start at all if your working copy has uncommitted
changes, so a proposed change never contains a mixture of your edits and
its own.

### 2. There are things it is not allowed to change about itself

`CONSTITUTION.md` in the repository root is the plain-English version.
`core/app/constitution.py` is the same list in code. Twenty paths are
protected, each with a stated reason:

- the Constitution itself, and the file listing what is protected
- the permission system, the approval record, and the permission definitions
- authentication, sessions, and the sign-in routes
- the emergency stop and the spending ceiling
- the isolation boundary and the self-development gates themselves
- database migrations, container and deployment configuration
- anything matching `.env*`, `*.pem`, `*.key`

This is enforced in two places, deliberately:

- **Before the model is asked anything.** The planner prompt is given the
  list in plain words, so it is not being tricked into failing.
- **At the write boundary.** Every single write goes through one function
  that refuses a protected path. If the planner names one anyway, that
  file is stripped out of the plan and appears in the "cannot" list, so
  the refusal is something you can see rather than something that happens
  silently.

Path-spelling tricks are handled: `core//app//constitution.py`,
`./core/app/constitution.py`, `/core/app/constitution.py` and backslash
variants all resolve to the same protected file, and anything containing
`..` is refused outright rather than resolved.

`/health` reports a fingerprint of the Constitution. If the file changes,
the fingerprint changes, and you can see that it did.

### 3. It is honest about what this does not defend against

The Constitution ends by saying so plainly: it defends against **JARVIS's
own self-development**. It does not defend against anyone with shell
access to the server. A protection that overstates itself is worse than
none, because you stop watching the thing it claimed to cover.

### 4. Reasoning that is never actionable

The Constitution names the specific arguments that are not allowed to
lead anywhere, regardless of how well-formed they are:

- "I could be more capable if I removed a restriction."
- "Protected authority is not a performance parameter."

This exists because a capable system asked to improve itself will
eventually notice that its own limits are the cheapest thing to improve.

### 5. A stated model preference is obeyed or refused, never ignored

Three modes, read from how you phrase it:

| You say | Mode | What happens |
|---|---|---|
| "Use Claude **only** for this" | HARD | If Claude is unavailable, **nothing runs**. You get the reason and a 503. |
| "**Preferably** Claude, but either is fine" | SOFT | It falls back, and the reply tells you it fell back. |
| (nothing about a model) | NONE | JARVIS chooses. |

Verified live: a hard override for an unconfigured provider returns

> You asked for Claude and I cannot use it: Claude is not configured on
> this deployment: its API key is not set. Nothing has been run. Say
> which provider to use instead, or drop the restriction and I will
> choose.

Choosing a model grants nothing else. It does not bypass the Governor,
the Constitution, permissions, the protected core, or the audit record —
there is a test named for exactly that.

### 6. The gate is asked between waves, not after

An earlier version of the approval gate checked *after* the whole plan had
run, which made it a report rather than a gate. The orchestrator now takes
a `gate` callback and asks it between waves of work, and cancels what is
left if the answer is no.

---

## What is not built yet

Stated plainly so this document is not a sales page.

| Stage | Item | Status |
|---|---|---|
| C | Risk classification (Levels 0–4) mapped onto change requests | **Not built.** Every change is treated the same today. |
| F | JARVIS Scientist and Auditor agents | **Not built.** There is a hook in the org chart that will file a `scientist.*` capability under governance when one exists. |
| G | UI self-redesign | **Untested.** The pipeline can reach `static/`, but this has never been run. |
| H | Rollback / last-known-good | **Not built.** The agent registry keeps version history, which is the raw material, but there is no deployment rollback. |
| I | Docker build verification inside the pipeline | **Not run.** `docker build` has never been executed against this repository from here -- there is no Docker daemon in the environment these changes were written in. The image is checked by tests that read the Dockerfile and the ignore files, which is not the same thing. |
| — | Model router provider awareness | `choose()` has no `provider` parameter yet. |
| — | Tier 0–4 capability-first routing, local utility layer | Not built. |
| — | Browser agent, search policy modes, computer access | Not built. |
| — | Model performance learning from `agent_metrics` | Not built. The metrics are being recorded; nothing reads them back. |

Tests 2, 3, 4, 5, 7, 9, 10, 11, 12, 15, 17 and 18 from the brief are
covered. Test 1 is half-covered -- see the caveat below. Tests 6, 8, 14
and 16 are not, because they test the stages above.

### The caveat that matters most

**The pipeline has never produced a real change with a real model.**
Every part of it is unit-tested, and the isolation is proven, but every
run so far has used the mock provider. The first time a real model plans
a real change is still ahead, and that is where the interesting failures
will be.

---

---

## The container does not run as root

Found by reading the Dockerfile while answering "is the prompt actually
implemented" -- not by any test, which is the uncomfortable part.

The image had no `USER` directive, so JARVIS ran as root inside its own
container. Root in a container is not root on your server; it is one
container escape away from it, and there was no reason to be holding it.

What is now true:

- The image creates a normal user and drops to it before it starts.
- **`/app` is left owned by root.** JARVIS can read its own source code
  and cannot write it. That is the Constitution's boundary enforced a
  second time, by the filesystem, rather than by JARVIS's own good
  behaviour.
- The Docker socket is not mounted, nothing is `privileged`, and
  `no-new-privileges` is set, so a setuid binary cannot climb back up.

Seven tests guard this, and each one was checked by breaking the thing it
guards and confirming it failed: no `USER` line, a user that is never
created, dropping privileges before `pip install`, returning to root
afterwards, handing `/app` to the runtime user, mounting the Docker
socket, and `user: root` in compose.

Still deliberately not done: `read_only: true` on the container. It is
the right next step and it needs a real `docker build` to confirm
nothing breaks, which has not been run.

---

## How to change the Constitution

By hand, by Sagar, in an editor. There is no command, no endpoint and no
approval flow for it. That is the whole point: a rule the system can
change through its own normal machinery is not a rule, it is a setting.
