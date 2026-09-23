"""Turning "play Kesariya on YouTube" into something that actually plays.

`browse.py` resolves the owner's words into an address without touching
the network -- deliberately, so opening something is instant, free, and
cannot be steered by anything JARVIS has read. For "open YouTube" that
address is the right one and the job is done.

For "PLAY Kesariya on YouTube" it is not. The best a pure resolver can do
is a search results page, and a search results page is a list he then has
to look at and tap. He said "play". YouTube opened. Nothing played. That
is a correct URL and a wrong answer, and from the sofa it looks exactly
like a broken assistant.

So this one case gets a network call: fetch the results, take the first
video, and hand back `watch?v=...`, which plays on open and hands off to
the YouTube app on an iPad.

**Everything about it is optional.** It runs after the address is already
resolved, with a short timeout, and any failure at all -- no network, a
changed page, a slow day -- leaves the search URL exactly as it was. The
owner gets the old behaviour rather than an error, which is the only
sensible trade for a nicety that costs a round trip.

**Nothing here comes from a model or a page.** The query is his own
words, the domain is fixed, and the only thing read out of the response
is an eleven-character video id matched by a strict pattern. A page
cannot talk this into fetching somewhere else.
"""
import logging
import re

import httpx

from app.agents.tools import fetch

logger = logging.getLogger("jarvis.play")

# Short. This runs between him asking and the tab opening, and a second
# of silence is the whole budget. Past that the search page is better
# than a wait.
TIMEOUT = 4.0

# Enough of the page to hold the first results. The whole thing is
# megabytes of player configuration nobody here reads.
MAX_BYTES = 600_000

# Exactly eleven of YouTube's alphabet, and nothing else. Anything
# looser would match ids out of advertising blobs and unrelated JSON.
VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')

# The sites where "play" can be made to really play. Spotify is not one
# of them: its web player needs a signed-in Premium account, and its
# search URL is already the best a link can do.
RESOLVES = {"YouTube"}


def first_video(html: str) -> str | None:
    """The first real video id on a results page, or None.

    The first match rather than the most common one: YouTube puts the
    top result first and repeats every id many times in its player
    configuration, so counting would pick whatever appears in the most
    markup rather than what he asked for.
    """
    found = VIDEO_ID.search(html or "")
    return found.group(1) if found else None


async def playable(site: str, url: str, query: str | None) -> str | None:
    """A URL that plays, or None to keep the one we already had.

    Never raises. The caller has a perfectly good search URL in hand and
    an exception here would turn a nicety into a failed request.
    """
    if not query or site not in RESOLVES or not url:
        return None

    try:
        # The same address check every other outbound fetch goes through.
        # The domain is fixed and ours, so this is belt and braces -- but
        # a fetch that skips the check because "this one is fine" is how
        # the next one skips it too.
        fetch.check_address(url)
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            response = await client.get(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; JARVIS/1.0)",
                              "Accept-Language": "en"})
        if response.status_code >= 400:
            logger.info("YouTube answered %s looking for %r.",
                        response.status_code, query)
            return None
        video = first_video(response.text[:MAX_BYTES])
    except Exception as exc:  # noqa: BLE001 - a nicety must never fail a reply
        logger.info("Could not resolve something to play for %r: %s", query, exc)
        return None

    if not video:
        logger.info("No video found on the results page for %r.", query)
        return None
    return f"https://www.youtube.com/watch?v={video}"
