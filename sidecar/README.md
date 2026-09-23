# JARVIS sidecar

JARVIS runs on your server. The server has no screen, no browser of
yours, and no access to your Mac or PC. A sidecar gives it **exactly**
the abilities you choose on one of those devices, and nothing else.

There are two kinds, because a phone is not a laptop.

## On an iPad or a phone — nothing to install

**You cannot run a background program on iOS.** Apple does not allow it,
and no wrapper, shortcut or trick changes that. Anyone who tells you
otherwise is describing something that stops the moment the screen locks.

What an iPad *does* have is this dashboard, already open. So the tab
itself becomes the sidecar:

**Build → What JARVIS may reach → This device → Use this device.**

One tap. No code, no install. From then on, while that tab is on screen,
JARVIS can speak through it, show you things, and put links in front of
you to tap.

What it can do there, and nothing more:

| | |
|---|---|
| `speak` | Say something aloud |
| `notify` | Show you a message — in the page always; as a real notification only if you add JARVIS to your Home Screen first |
| `browser` | Put a link in front of you. **Not** a tab opened behind your back: a web page cannot do that without your tap, and pretending otherwise is how you end up with JARVIS claiming to have opened something that never opened |
| `clipboard` | Copy something for you, when Safari allows it |

**It works while the tab is on screen.** A hidden tab is one iOS is about
to suspend, and a sidecar that claimed to be listening with the screen
off would be lying. The useful exception: on an iPad in **Split View**,
iPadOS keeps both apps awake — so JARVIS beside Safari or Notes keeps
working. That is how to actually use it.

## On a Mac, PC, Linux box or Pi — the full version

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

## What the full version can be given

| | |
|---|---|
| `screenshot` | See what is on that screen |
| `browser` | Open an address in that machine's browser |
| `files.read` | Read files on that machine |
| `files.write` | Write files on that machine — **always asks you first** |
| `terminal` | Run commands on that machine — **always asks you first** |
| `clipboard` | Read and set that machine's clipboard |
| `notify` | Show a notification on that machine |
| `speak` | Say something aloud on that machine |

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

## When you move JARVIS to another server

Moving JARVIS takes its database with it, so the pairing and the token
survive the move — only the address changes:

```bash
python3 jarvis_sidecar.py repoint https://jarvis.athome.lan
```

It saves the new address and immediately checks that the server there
still knows this sidecar, so you find out now rather than the first time
you need it. If JARVIS was moved *without* its database, it says so and
you pair again.

Browser sidecars need nothing: the dashboard is the sidecar, so
whichever address you open is the one it talks to.

## Where the token lives

`~/.jarvis-sidecar.json`, readable only by you. The server keeps only a
hash of it, so it cannot be read back out of JARVIS by anybody —
including you. Lose it and you pair again, which is cheap.
