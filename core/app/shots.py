"""Screenshots the browser took, kept just long enough to be looked at.

A picture of a page is the one thing a text answer cannot be. It is also
a file JARVIS produced from something it read on the internet, so it is
kept where the owner can see it and nowhere else: behind the same sign-in
as everything else, named unguessably, and swept up after a day.

Deliberately not in the database. A screenshot is between 30KB and a few
megabytes, it is worth nothing after the conversation it belongs to, and
putting it in Postgres would mean backing it up for ever.
"""
import logging
import os
import re
import secrets
import time
from pathlib import Path

logger = logging.getLogger("jarvis.shots")

# Under /tmp by default: the container's writable scratch, swept by the
# host if JARVIS never gets to it.
DIRECTORY = Path(os.environ.get("SHOTS_DIR", "/tmp/jarvis-shots"))

# How long a screenshot is worth keeping. Long enough to look at on the
# phone later; not long enough to become a collection of everything
# JARVIS has ever browsed.
KEEP_HOURS = 24

# What a name may be, checked on the way out as well as chosen on the way
# in. A name is used to build a path, so this is the thing standing
# between a request and `../../etc/passwd`.
NAME = re.compile(r"^[0-9a-f]{32}\.png$")


async def keep(png: bytes, task_id=None) -> str:
    """Write one, sweep the old ones, and return where to find it."""
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}.png"
    (DIRECTORY / name).write_bytes(png)
    _sweep()
    logger.info("Kept a screenshot for task %s as %s", task_id, name)
    return f"/v1/shots/{name}"


def path_for(name: str) -> Path | None:
    """The file, or None. Never builds a path out of an unchecked name."""
    if not NAME.fullmatch(name or ""):
        return None
    candidate = DIRECTORY / name
    # Belt and braces, and both braces were checked: with either one of
    # these removed on its own, every path-escape case is still refused
    # by the other; with both removed they get through. So neither is
    # redundant, they cover each other. The pattern is also the only
    # thing that refuses a well-formed name this code did not generate,
    # and this is the only thing that would still hold if the pattern
    # were ever loosened.
    if candidate.parent.resolve() != DIRECTORY.resolve():
        return None
    return candidate if candidate.is_file() else None


def _sweep() -> None:
    cutoff = time.time() - KEEP_HOURS * 3600
    try:
        for old in DIRECTORY.glob("*.png"):
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001 - tidying must never break saving
        logger.info("Could not sweep old screenshots: %s", exc)
