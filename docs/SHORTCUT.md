# Giving JARVIS a hand on your iPad

JARVIS runs on a server in another room. It has no screen, no speakers,
no timer, no messages app and no lights. Opening a web address covers
more than you would think — Spotify, YouTube, Netflix, LinkedIn all open
straight into their app from a link — and then it stops dead, because a
timer is not a web address.

On an iPad or iPhone, the thing that *can* do those is **Shortcuts**. You
build one shortcut, once, called `JARVIS`. After that JARVIS can ask it
to do anything you taught it, just by saying so.

## Why it works this way

The obvious design is a small program on your iPad that takes orders from
the server. It is also the wrong one, and worth understanding why.

Such a program would mean the server could act on your iPad whenever it
liked — and if the server were ever compromised, so was the iPad. There
is no version of that which is safe on a machine reachable from the
internet.

This cannot do that. The link is opened by a page **you** are looking at,
in response to something **you** just typed or said. iOS asks you the
first time. There is no channel from the server to your device that you
are not standing in front of.

The price is honest: it only works while you have JARVIS open. That is
the correct price, and it is not a limitation that better code removes.

## Build the shortcut

About five minutes, once.

1. Open **Shortcuts** on your iPad → **+** (new shortcut).
2. Rename it to exactly **JARVIS** (tap the name at the top). The name
   is how JARVIS finds it, so the spelling matters.
3. Add action: **Get Text from Input**. (Search "text from input".)
4. Add action: **If**. Set it to: `Text` **contains** `timer`.
5. Inside the If, add **Start Timer**. Tap the duration and set it from
   the text, or leave a default like 10 minutes to begin with.
6. Tap **Otherwise**, and add another **If** for the next thing you want
   — `reminder`, `message`, `light`, `pause`, `volume`.
7. Save.

Then in JARVIS, say *"set a timer for 10 minutes"*. The first time, iOS
will ask whether to allow it. Say yes.

## Start smaller if that looks like a lot

It does look like a lot written down. A shortcut that handles exactly one
thing is a fine place to start:

1. New shortcut, named **JARVIS**.
2. One action: **Start Timer**, 10 minutes.
3. Save.

Now *"set a timer for 10 minutes"* works. Add the others when you want
them; JARVIS does not need to know what is inside.

## What JARVIS will hand it

Whatever you said, as plain text. Say *"remind me to call the bank at
four"* and the shortcut receives exactly that sentence. Your shortcut
decides what to do with it — so what JARVIS can do on your iPad is
bounded by two things, both of them yours: what you asked for, and what
you built the shortcut to handle.

Nothing a model wrote ever becomes the instruction. That is deliberate,
and it is the same rule that governs opening a web address:
`core/app/browse.py` resolves both from your words, before any model is
called.

## What needs no shortcut at all

These already work, on any device, with nothing installed:

| Say | What happens |
|---|---|
| "Open YouTube" | YouTube opens |
| "Play Blinding Lights on Spotify" | Spotify opens on that search |
| "Put on Interstellar on Netflix" | Netflix opens on that search |
| "Search LinkedIn for AI hiring" | That search opens |
| "Open bbc.co.uk" | That site opens |

On an iPad those links hand off to the installed app rather than the
website, because they are universal links.

## If it does not work

**"There is no shortcut named JARVIS."** The name must match exactly —
capitals included.

**Nothing happens when you tap.** Safari blocks links that open other
apps unless you tapped something. JARVIS leaves a button for exactly
that; tap it.

**It runs but does the wrong thing.** That is inside your shortcut, not
JARVIS. Open Shortcuts, run it by hand with the same text, and watch
where it goes.
