"""Opening things. "Open YouTube" should open YouTube.

**Where the address comes from, which is the whole security design.**
The URL is resolved from the owner's own words, here, before any model is
called. The model never chooses it and never sees a chance to.

That matters more than it looks. JARVIS reads documents the owner
attaches, search results, and web pages. If a model's reply could say
"and open this address", then anything JARVIS reads could steer his
browser -- a line in a PDF, a sentence on a page it was researching. By
resolving the address from the instruction rather than from the answer,
that path does not exist rather than being guarded against.

It also means this is instant and free: no model call, no tokens, no
waiting. "Open YouTube" is not a question that needs thinking about.

**What actually opens it.** JARVIS runs on a server in another room; it
has no screen. So it does not open a browser -- it tells the page in
front of the owner to open a tab. The thing that opens is his browser,
on his device, which is what he meant.
"""
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

# The sites worth knowing by name. Everything else falls through to the
# domain rules below, so this list is a convenience, not a limit.
SITES: dict[str, tuple[str, str]] = {
    "youtube": ("YouTube", "https://www.youtube.com"),
    "yt": ("YouTube", "https://www.youtube.com"),
    "google": ("Google", "https://www.google.com"),
    "gmail": ("Gmail", "https://mail.google.com"),
    "drive": ("Google Drive", "https://drive.google.com"),
    "calendar": ("Google Calendar", "https://calendar.google.com"),
    "maps": ("Google Maps", "https://www.google.com/maps"),
    "linkedin": ("LinkedIn", "https://www.linkedin.com"),
    "github": ("GitHub", "https://github.com"),
    "whatsapp": ("WhatsApp", "https://web.whatsapp.com"),
    "instagram": ("Instagram", "https://www.instagram.com"),
    "insta": ("Instagram", "https://www.instagram.com"),
    "twitter": ("X", "https://x.com"),
    "reddit": ("Reddit", "https://www.reddit.com"),
    "wikipedia": ("Wikipedia", "https://www.wikipedia.org"),
    "amazon": ("Amazon", "https://www.amazon.in"),
    "flipkart": ("Flipkart", "https://www.flipkart.com"),
    "netflix": ("Netflix", "https://www.netflix.com"),
    "spotify": ("Spotify", "https://open.spotify.com"),
    "music": ("Apple Music", "https://music.apple.com"),
    "applemusic": ("Apple Music", "https://music.apple.com"),
    "prime": ("Prime Video", "https://www.primevideo.com"),
    "primevideo": ("Prime Video", "https://www.primevideo.com"),
    "hotstar": ("JioHotstar", "https://www.hotstar.com"),
    "photos": ("Google Photos", "https://photos.google.com"),
    "keep": ("Google Keep", "https://keep.google.com"),
    "sheets": ("Google Sheets", "https://sheets.google.com"),
    "docs": ("Google Docs", "https://docs.google.com"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com"),
    "claude": ("Claude", "https://claude.ai"),
    "gemini": ("Gemini", "https://gemini.google.com"),
}

# Where a search goes, for the sites where searching is the point.
# Universal links, deliberately, not app URL schemes.
#
# `spotify:search:...` is the obvious choice and it does not work: the
# scheme is undocumented for search, community reports have it failing
# with "Spotify can't open this type of link on this device", and it
# breaks outright for anyone without the app installed. An
# https://open.spotify.com link is a universal link -- iOS hands it to
# the app when it is installed and falls back to the web page when it is
# not. One address, both outcomes, no setup.
SEARCH: dict[str, str] = {
    "Spotify": "https://open.spotify.com/search/{q}",
    "Apple Music": "https://music.apple.com/search?term={q}",
    "Prime Video": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    "Netflix": "https://www.netflix.com/search?q={q}",
    "JioHotstar": "https://www.hotstar.com/in/explore?search_query={q}",
    "YouTube": "https://www.youtube.com/results?search_query={q}",
    "Google": "https://www.google.com/search?q={q}",
    "LinkedIn": "https://www.linkedin.com/search/results/all/?keywords={q}",
    "GitHub": "https://github.com/search?q={q}",
    "Amazon": "https://www.amazon.in/s?k={q}",
    "Flipkart": "https://www.flipkart.com/search?q={q}",
    "Reddit": "https://www.reddit.com/search/?q={q}",
    "Wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "Spotify": "https://open.spotify.com/search/{q}",
}

# "Open YouTube", "go to gmail", "launch linkedin", "pull up maps".
_OPEN = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:open|go\s+to|goto|launch|visit|bring\s+up|pull\s+up|take\s+me\s+to|"
    r"show\s+me)\s+"
    r"(?:the\s+)?(?:website\s+|site\s+|page\s+)?"
    r"(.+?)"
    r"\s*(?:\bfor\s+me\b)?\s*[.!?]*\s*$",
    re.I,
)

# "search youtube for cats", "search for cats on youtube".
_SEARCH_ON = re.compile(
    r"^\s*(?:please\s+)?search\s+(?:on\s+)?(\w+)\s+for\s+(.+?)\s*[.!?]*\s*$",
    re.I,
)
_SEARCH_FOR = re.compile(
    r"^\s*(?:please\s+)?search\s+for\s+(.+?)\s+on\s+(\w+)\s*[.!?]*\s*$",
    re.I,
)

# "open youtube and search for cats", "open youtube and play jazz".
_AND_SEARCH = re.compile(
    r"\s+and\s+(?:search\s+(?:for\s+)?|look\s+for\s+|find\s+|play\s+)(.+)$",
    re.I,
)

# "Play Blinding Lights on Spotify", "play lofi beats on YouTube".
#
# The service is named because guessing it is worse than asking. "Play
# something" with no service is not a browser instruction -- it is a
# conversation, and it goes to the model like any other sentence.
_PLAY_ON = re.compile(
    r"^\s*(?:please\s+)?(?:play|put\s+on|listen\s+to|watch)\s+"
    r"(.+?)\s+(?:on|in|using|with|through)\s+(\w[\w\s]*?)\s*[.!?]*\s*$",
    re.I,
)
# "Play Spotify" / "put on YouTube" -- the service alone, nothing to find.
_PLAY_BARE = re.compile(
    r"^\s*(?:please\s+)?(?:play|put\s+on|open)\s+(\w+)\s*[.!?]*\s*$",
    re.I,
)

# "...in Chrome", "...using Safari". Stripped off before the rest is
# read, because it says WHERE to open something, not what.
BROWSERS = {
    "chrome": "chrome", "google chrome": "chrome",
    "safari": "safari", "default": "safari",
}
_IN_BROWSER = re.compile(
    r"\s+(?:in|using|with|on|via)\s+(google\s+chrome|chrome|safari)\s*[.!?]*\s*$",
    re.I,
)

# A bare address: "open bbc.co.uk", "open example.com/page".
_DOMAIN = re.compile(
    r"^(?:https?://)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(/[^\s]*)?$", re.I
)

# Schemes that are not a website. `javascript:` and `file:` typed by the
# owner would only be him attacking himself, but they are refused anyway
# because there is no version of "open YouTube" that needs them.
_BAD_SCHEME = re.compile(r"^\s*(javascript|data|file|vbscript|about)\s*:", re.I)


# Where "play" means something. Everywhere else, "play X on Y" is a
# sentence rather than an instruction.
PLAYABLE = frozenset({"Spotify", "YouTube", "Apple Music", "Netflix",
                      "Prime Video", "JioHotstar"})


@dataclass(frozen=True)
class Open:
    """One address to open, and what to say about it."""
    url: str
    site: str
    query: str | None = None
    # Which browser he named, if he named one. The page turns this into
    # the right address: https for Safari, googlechromes:// for Chrome.
    browser: str | None = None

    @property
    def said(self) -> str:
        if self.query:
            return f"Opening {self.site} and searching for {self.query}."
        return f"Opening {self.site}."

    def as_detail(self) -> dict:
        return {"url": self.url, "site": self.site, "query": self.query,
                "browser": self.browser, "said": self.said}


def _search_url(site: str, query: str) -> str | None:
    template = SEARCH.get(site)
    return template.format(q=quote_plus(query)) if template else None


def _resolve(target: str, query: str | None = None) -> Open | None:
    """One name or address, to something openable. None if it is neither."""
    target = target.strip().strip("\"'").rstrip(".,!?")
    if not target or _BAD_SCHEME.match(target):
        return None

    key = target.lower().replace(" ", "")
    known = SITES.get(key) or SITES.get(key.removesuffix(".com"))
    if known:
        name, home = known
        if query:
            searched = _search_url(name, query)
            if searched:
                return Open(url=searched, site=name, query=query)
            # No search for this one -- open it rather than refusing, and
            # say only what is true.
            return Open(url=home, site=name)
        return Open(url=home, site=name)

    domain = _DOMAIN.match(target)
    if domain:
        host = domain.group(1).lower()
        path = domain.group(2) or ""
        return Open(url=f"https://{host}{path}", site=host)

    return None


# --- things a web address cannot do --------------------------------------
#
# Opening a page covers a surprising amount, and then stops dead. A timer,
# a reminder, a message, a light -- none of those are a URL.
#
# On iPad the hand for that is Shortcuts. A shortcut can be run from a
# link, which means JARVIS can trigger one the same way it opens a tab:
#
#   shortcuts://x-callback-url/run-shortcut?name=JARVIS&input=text
#       &text=<instruction>&x-success=<back to JARVIS>
#
# This is the whole of the "companion on your device" idea, and it is
# worth being clear about why it is shaped this way rather than as a
# program JARVIS talks to.
#
# A program on his iPad taking orders from the server would mean the
# server could act on the iPad whenever it liked -- and if the server
# were ever compromised, so was the iPad. This cannot. The link is
# opened by a page HE is looking at, in response to something HE typed or
# said, and iOS asks him the first time. There is no channel from the
# server to the device that he is not standing in front of.
#
# The price is honest: it only works while he has JARVIS open. That is
# the correct price.
SHORTCUT = "JARVIS"

# What gets handed to that one shortcut, as plain text it can switch on.
# One shortcut rather than one per action, because installing a shortcut
# is the only setup step here and asking for ten of them is asking for
# none.
_DO = re.compile(
    r"^\s*(?:please\s+)?("
    r"set\s+an?\s+(?:timer|alarm|reminder)\b.*|"
    r"remind\s+me\b.*|"
    r"(?:send\s+a\s+)?(?:message|text|whatsapp)\s+\w+.*|"
    r"turn\s+(?:on|off)\s+the\s+\w+.*|"
    r"(?:pause|resume|skip|next\s+track|previous\s+track)\b.*|"
    r"(?:turn\s+)?volume\s+(?:up|down|to)\b.*"
    r")\s*$",
    re.I,
)


def shortcut_url(instruction: str, back_to: str = "") -> str:
    """The link that runs the one shortcut, with what to do in it."""
    from urllib.parse import quote

    url = (f"shortcuts://x-callback-url/run-shortcut"
           f"?name={quote(SHORTCUT)}&input=text&text={quote(instruction)}")
    if back_to:
        url += f"&x-success={quote(back_to, safe='')}"
    return url


def needs_the_shortcut(said: str) -> str | None:
    """The instruction to hand the shortcut, or None if this is not one.

    Read from his words, exactly like an address is. The shortcut is
    handed the sentence he actually said, so what it does is bounded by
    what he asked for and by what he built the shortcut to handle -- not
    by anything a model produced.
    """
    text = _plainly(said)
    if not text or len(text) > 300:
        return None
    match = _DO.match(text)
    return match.group(1).strip() if match else None


# How a person actually addresses an assistant, which is not how an
# imperative is written.
#
# Every pattern in this file was built around the bare form -- "Open
# YouTube" -- and every test used the bare form, so the tests agreed
# with the assumption instead of checking it. In use, "Jarvis, open
# YouTube" and "can you open YouTube" both fell straight through to the
# model, which then correctly reported that it could not open anything.
#
# Stripped in a loop, because these stack: "Hey Jarvis, could you please
# open YouTube" is one sentence with four of them on the front.
_ADDRESS = re.compile(
    r"^\s*(?:"
    r"(?:hey|ok|okay|hi|yo)\s+jarvis|jarvis|"
    r"can\s+you|could\s+you|would\s+you|will\s+you|"
    r"i\s+(?:want|need)\s+you\s+to|i\s+want\s+to|"
    r"please|kindly|"
    r"let(?:'|’)?s"
    r")\b[\s,:.\u2014-]*",
    re.I,
)
# And off the end: "open youtube for me", "open youtube please".
_TRAILING = re.compile(
    r"[\s,]*(?:for\s+me|please|thanks|thank\s+you)\s*[.!?]*\s*$", re.I
)


def _plainly(said: str) -> str:
    """The instruction with the politeness taken off."""
    text = (said or "").strip()
    for _ in range(5):          # bounded: four stacked is already unusual
        shorter = _ADDRESS.sub("", text, count=1)
        shorter = _TRAILING.sub("", shorter, count=1).strip()
        if shorter == text:
            break
        text = shorter
    return text


def read(said: str) -> Open | None:
    """What the owner asked to open, or None if he did not ask for that.

    Deliberately strict. "Open a bank account" and "show me the media
    page" are not browser instructions, and a loose match here would send
    him somewhere absurd instead of answering him.
    """
    text = _plainly(said)
    if not text or len(text) > 300:
        return None

    # "open YouTube in Chrome" -- take the browser off first, so every
    # pattern below sees the same sentence it always did.
    browser = None
    named = _IN_BROWSER.search(text)
    if named:
        browser = BROWSERS.get(" ".join(named.group(1).lower().split()))
        text = text[: named.start()].strip()

    found = _read(text)
    if found is None or browser is None:
        return found
    return Open(url=found.url, site=found.site, query=found.query,
                browser=browser)


def _read(text: str) -> Open | None:
    """The reading itself, once the browser has been taken off."""
    # Play first: "play X on Y" is unambiguous, and the open patterns
    # below would otherwise never see it anyway.
    match = _PLAY_ON.match(text)
    if match:
        wanted, service = match.group(1).strip(), match.group(2).strip()
        found = _resolve(service, wanted)
        # Only for somewhere you can actually play something. "Play the
        # invoice on LinkedIn" resolves to a LinkedIn search, which is
        # not what anybody meant.
        if found and found.site in PLAYABLE:
            return found
        return None

    match = _SEARCH_ON.match(text)
    if match:
        return _resolve(match.group(1), match.group(2))

    match = _SEARCH_FOR.match(text)
    if match:
        return _resolve(match.group(2), match.group(1))

    match = _OPEN.match(text)
    if not match:
        return None

    target = match.group(1).strip()
    query = None
    trailing = _AND_SEARCH.search(target)
    if trailing:
        query = trailing.group(1).strip()
        target = target[: trailing.start()].strip()

    return _resolve(target, query)
