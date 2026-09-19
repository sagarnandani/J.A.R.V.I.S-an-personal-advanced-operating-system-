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
    "chatgpt": ("ChatGPT", "https://chatgpt.com"),
    "claude": ("Claude", "https://claude.ai"),
    "gemini": ("Gemini", "https://gemini.google.com"),
}

# Where a search goes, for the sites where searching is the point.
SEARCH: dict[str, str] = {
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

# A bare address: "open bbc.co.uk", "open example.com/page".
_DOMAIN = re.compile(
    r"^(?:https?://)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(/[^\s]*)?$", re.I
)

# Schemes that are not a website. `javascript:` and `file:` typed by the
# owner would only be him attacking himself, but they are refused anyway
# because there is no version of "open YouTube" that needs them.
_BAD_SCHEME = re.compile(r"^\s*(javascript|data|file|vbscript|about)\s*:", re.I)


@dataclass(frozen=True)
class Open:
    """One address to open, and what to say about it."""
    url: str
    site: str
    query: str | None = None

    @property
    def said(self) -> str:
        if self.query:
            return f"Opening {self.site} and searching for {self.query}."
        return f"Opening {self.site}."

    def as_detail(self) -> dict:
        return {"url": self.url, "site": self.site, "query": self.query,
                "said": self.said}


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


def read(said: str) -> Open | None:
    """What the owner asked to open, or None if he did not ask for that.

    Deliberately strict. "Open a bank account" and "show me the media
    page" are not browser instructions, and a loose match here would send
    him somewhere absurd instead of answering him.
    """
    text = (said or "").strip()
    if not text or len(text) > 300:
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
