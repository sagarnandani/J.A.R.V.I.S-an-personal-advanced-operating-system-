"""Opening things, and the one property that makes it safe to have.

The address is resolved from what the owner typed. It never comes from a
model's reply. That is not a detail -- JARVIS reads attachments, search
results and web pages, and if a reply could say "open this address" then
anything it reads could steer his browser. The last test in this file is
the one that matters; the rest are about not opening absurd things.
"""
import pytest

from app import browse


# --- what it opens ---------------------------------------------------------

@pytest.mark.parametrize("said,expected", [
    ("Open YouTube", "https://www.youtube.com"),
    ("open youtube", "https://www.youtube.com"),
    ("OPEN YOUTUBE", "https://www.youtube.com"),
    ("go to gmail", "https://mail.google.com"),
    ("goto linkedin", "https://www.linkedin.com"),
    ("launch github", "https://github.com"),
    ("visit reddit", "https://www.reddit.com"),
    ("pull up maps", "https://www.google.com/maps"),
    ("take me to netflix", "https://www.netflix.com"),
    ("Please open WhatsApp.", "https://web.whatsapp.com"),
    ("open the website linkedin", "https://www.linkedin.com"),
    ("Open LinkedIn for me", "https://www.linkedin.com"),
])
def test_it_opens_what_was_asked_for(said, expected):
    opened = browse.read(said)
    assert opened is not None, f"{said!r} was not recognised"
    assert opened.url == expected


def test_an_address_it_has_never_heard_of_still_opens():
    """The list of known sites is a convenience, not a limit."""
    assert browse.read("open bbc.co.uk").url == "https://bbc.co.uk"
    assert browse.read("open example.com/news").url == "https://example.com/news"
    assert browse.read("open https://example.org").url == "https://example.org"


def test_searching_goes_to_the_search_page():
    assert browse.read("search youtube for lofi beats").url == (
        "https://www.youtube.com/results?search_query=lofi+beats")
    assert browse.read("search for lofi beats on youtube").url == (
        "https://www.youtube.com/results?search_query=lofi+beats")
    assert browse.read("open youtube and play lofi beats").url == (
        "https://www.youtube.com/results?search_query=lofi+beats")


def test_a_query_is_escaped_rather_than_pasted_in():
    """An unescaped & would end the query parameter and turn the rest of
    what he said into separate parameters of the search page's choosing."""
    opened = browse.read("search google for c++ & rust")
    query = opened.url.split("?", 1)[1]
    assert query.startswith("q=")
    value = query[2:]
    assert "&" not in value, f"the query was not escaped: {value}"
    assert " " not in value
    assert "c%2B%2B" in value
    assert "rust" in value


def test_searching_a_site_with_no_search_opens_it_and_says_only_that():
    """Never claim a search that did not happen."""
    opened = browse.read("open netflix and search for dune")
    assert opened.url == "https://www.netflix.com"
    assert opened.query is None
    assert "searching" not in opened.said


# --- what it refuses -------------------------------------------------------

@pytest.mark.parametrize("said", [
    "Open a bank account",
    "open the pod bay doors",
    "open up about your feelings",
    "what is on the media page",
    "show me the weather",
    "can you open this for me",          # nothing named
    "",
    "   ",
])
def test_it_does_not_open_things_that_were_not_addresses(said):
    assert browse.read(said) is None


@pytest.mark.parametrize("said", [
    "open javascript:alert(1)",
    "open data:text/html,<script>x</script>",
    "open file:///etc/passwd",
    "go to vbscript:msgbox",
    "open about:config",
])
def test_it_refuses_anything_that_is_not_a_website(said):
    """Typed by the owner these would only be him attacking himself, but
    there is no version of "open YouTube" that needs them."""
    assert browse.read(said) is None


def test_a_very_long_message_is_not_an_instruction_to_open_something():
    assert browse.read("open " + "x" * 400) is None


# --- the property this exists for ------------------------------------------

def test_the_address_can_only_come_from_the_owners_own_words():
    """The security design, asserted on the source.

    `read` takes one string -- what he said -- and nothing else. There is
    no path by which a model's reply, a web page, or an attached document
    reaches it, because there is no parameter for one.
    """
    import inspect

    signature = inspect.signature(browse.read)
    assert list(signature.parameters) == ["said"]

    source = inspect.getsource(browse)
    for reachable in ("get_provider", "complete(", "llm", "requests.",
                      "httpx", "urlopen"):
        assert reachable not in source, (
            f"app/browse.py reaches {reachable!r} -- the address must be "
            f"resolved from the instruction, never fetched or generated"
        )


def test_the_reply_only_claims_what_actually_happens():
    """It says "opening", not "opened". JARVIS has no screen; the tab is
    opened by the browser in front of him, and may be blocked."""
    assert browse.read("open youtube").said == "Opening YouTube."
    assert browse.read("search youtube for jazz").said == (
        "Opening YouTube and searching for jazz.")
