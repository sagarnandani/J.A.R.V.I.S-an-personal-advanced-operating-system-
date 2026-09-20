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
    opened = browse.read("open gmail and search for invoices")
    assert opened.url == "https://mail.google.com"
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


# --- playing something -----------------------------------------------------

@pytest.mark.parametrize("said,expected", [
    ("Play Blinding Lights on Spotify",
     "https://open.spotify.com/search/Blinding+Lights"),
    ("play lofi beats on youtube",
     "https://www.youtube.com/results?search_query=lofi+beats"),
    ("put on Interstellar on Netflix",
     "https://www.netflix.com/search?q=Interstellar"),
    ("listen to Coltrane on Apple Music",
     "https://music.apple.com/search?term=Coltrane"),
    ("watch The Boys on Prime Video",
     "https://www.primevideo.com/search/ref=atv_nb_sr?phrase=The+Boys"),
])
def test_playing_something_lands_on_it(said, expected):
    played = browse.read(said)
    assert played is not None, f"{said!r} was not recognised"
    assert played.url == expected


def test_it_uses_universal_links_rather_than_app_schemes():
    """`spotify:search:...` is the obvious choice and it does not work --
    undocumented for search, reported failing with "Spotify can't open
    this type of link", and broken outright without the app. An
    open.spotify.com link is handed to the app by iOS when it is there
    and falls back to the web page when it is not."""
    addresses = ([home for _, home in browse.SITES.values()]
                 + list(browse.SEARCH.values()))
    for address in addresses:
        assert address.startswith("https://"), (
            f"{address} is not an https universal link -- an app URL "
            f"scheme breaks for anyone without the app installed"
        )

    # Grepping the module source instead of the addresses is what the
    # first version of this did, and it failed on the COMMENT explaining
    # why the scheme is avoided. A test that reads prose is testing prose.


@pytest.mark.parametrize("said", [
    "Play the invoice on LinkedIn",     # not somewhere you play things
    "play something",                   # nothing named
    "play chess",
    "play it again",
])
def test_play_somewhere_you_cannot_play_is_left_to_the_model(said):
    assert browse.read(said) is None


# --- what no address can do ------------------------------------------------

@pytest.mark.parametrize("said", [
    "set a timer for 10 minutes",
    "set an alarm for 6am",
    "remind me to call the bank at four",
    "message Priya I am running late",
    "turn off the bedroom light",
    "skip",
    "volume up",
])
def test_things_a_url_cannot_do_go_to_the_shortcut(said):
    assert browse.needs_the_shortcut(said) == said


@pytest.mark.parametrize("said", [
    "what is the weather",
    "open youtube",
    "Play Blinding Lights on Spotify",
    "how do I set a timer",              # a question, not an instruction
])
def test_ordinary_sentences_are_not_handed_to_the_shortcut(said):
    assert browse.needs_the_shortcut(said) is None


def test_the_shortcut_is_handed_what_he_said_and_nothing_else():
    """The bound on what JARVIS can do to his device is his own sentence
    plus whatever he built the shortcut to handle. Nothing a model wrote
    ever becomes the instruction."""
    import inspect

    signature = inspect.signature(browse.needs_the_shortcut)
    assert list(signature.parameters) == ["said"]


def test_the_shortcut_link_escapes_what_it_carries():
    url = browse.shortcut_url("remind me to buy milk & eggs",
                              "https://jarvis.local/?tab=home")
    query = url.split("?", 1)[1]
    assert "text=remind%20me%20to%20buy%20milk%20%26%20eggs" in query
    # The callback is one parameter, so its own ? and = must not end it.
    assert "x-success=https%3A%2F%2Fjarvis.local%2F%3Ftab%3Dhome" in query


def test_the_shortcut_is_named_the_same_everywhere():
    """The page builds this link too. If the two names drift, the
    failure is iOS saying there is no such shortcut."""
    from pathlib import Path

    page = Path(browse.__file__).parents[1] / "static" / "app.js"
    assert f'name={browse.SHORTCUT}' in page.read_text(), (
        "static/app.js builds the shortcut link with a different name "
        "from app/browse.py"
    )


# --- naming a browser ------------------------------------------------------

@pytest.mark.parametrize("said,browser", [
    ("open youtube in Chrome", "chrome"),
    ("open youtube in google chrome", "chrome"),
    ("open linkedin using chrome", "chrome"),
    ("open youtube in Safari", "safari"),
    ("Play Blinding Lights on Spotify in Chrome", "chrome"),
    ("Open YouTube", None),
])
def test_he_can_say_which_browser(said, browser):
    opened = browse.read(said)
    assert opened is not None, f"{said!r} was not recognised"
    assert opened.browser == browser


def test_naming_a_browser_does_not_change_where_it_goes():
    plain = browse.read("open youtube")
    named = browse.read("open youtube in Chrome")
    assert plain.url == named.url


def test_a_place_that_merely_sounds_like_a_browser_is_left_alone():
    """"open the shop in Chennai" must not be read as a browser, and
    must not be read as an address either."""
    assert browse.read("open the shop in Chennai") is None
    assert browse.read("open the file in Notepad") is None


def test_the_address_stays_https_and_the_page_converts_it():
    """Chrome's scheme is applied by the page, not stored here. The
    address JARVIS resolves is the canonical one, so a device with no
    Chrome still has something that works."""
    named = browse.read("open youtube in Chrome")
    assert named.url.startswith("https://")
    assert "googlechrome" not in named.url
