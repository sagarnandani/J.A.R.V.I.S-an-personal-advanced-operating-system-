# JARVIS sidecar

JARVIS runs on your server. The server has no screen, no browser of
yours, and no access to your Mac or PC. This is the small program you run
on those machines to give it **exactly** the abilities you choose, and
nothing else.

## Setting one up

On the dashboard: **Build → What JARVIS may reach → pair a machine.**
Choose a name and tick the capabilities. You get a code, good once and
for fifteen minutes.

Then on the other machine:

```bash
python3 jarvis_sidecar.py pair https://your-jarvis-address ABCD-1234-EF56
python3 jarvis_sidecar.py run
```

That is the whole thing. One file, standard library only — no install
step on a Mac, a PC or a Pi.

## What it can be given

| | |
|---|---|
| `screenshot` | See what is on that screen |
| `browser` | Open an address in that machine's browser |
| `files.read` | Read files on that machine |
| `files.write` | Write files on that machine — **always asks you first** |
| `terminal` | Run commands on that machine — **always asks you first** |
| `clipboard` | Read and set that machine's clipboard |
| `notify` | Show a notification on that machine |

Give each machine only what it needs. A Mac that takes screenshots is not
a Mac that has a terminal.

## The things worth understanding

**It connects out. JARVIS never dials into your laptop.** No port is
opened here, nothing is forwarded through your router, and it works from
a cafe. Closing the lid is the off switch, and nothing on the server can
override that — there is nothing here listening.

**Three things have to agree before anything runs.** What you granted
this machine, what the agent asking holds, and what the action needs.
Missing any one of them and the job is refused before it is even queued.

**This program refuses things too.** The server decides what to ask for;
this decides what it is willing to do, from the list you paired it with.
If the server ever asks for something outside that list — because it was
tampered with, or because of a bug — this says no and says so. That has
been tested by writing a `terminal` job straight into the database, past
the server's own check: the machine still refused it.

**There is no shell.** A command arrives as a list of arguments, never as
one string, so a semicolon is a semicolon. `file://` and `javascript:`
are not addresses this will open.

**Nothing runs for ever.** A job older than ten minutes expires rather
than running — a screenshot taken four hours late answers a question
nobody is still asking. Any single action is killed after sixty seconds.

## Stopping it

Ctrl-C, or close the terminal. JARVIS immediately cannot reach that
machine.

To stop it from the dashboard: **pause** (reversible) or **revoke**
(final — the token stops working, and pairing again is a new token).
Revoking cannot be undone by whoever is holding the laptop.

## Where the token lives

`~/.jarvis-sidecar.json`, readable only by you. The server keeps only a
hash of it, so it cannot be read back out of JARVIS by anybody —
including you. Lose it and you pair again, which is cheap.
