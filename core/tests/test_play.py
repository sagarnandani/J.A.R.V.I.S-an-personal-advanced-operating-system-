"""Making "play X" actually play.

"It opens but nothing happens next."

`browse.read` resolved "play Kesariya on YouTube" to a search results
page. That is a correct URL and a wrong answer: he said play, YouTube
opened, and a list of results appeared that he then had to look at and
tap. From the sofa that is a broken assistant.

Everything below is about the two halves of doing better: finding the
video, and -- more important -- never making things worse when that
fails. A nicety that costs a round trip must degrade to exactly the old
behaviour, not to an error.
"""
import socket

import httpx
import pytest

from app import play
from app.agents.tools import fetch

# Shaped like YouTube's real payload: the id repeated many times in the
# player configuration, with the top result first.
RESULTS = (
    '<!DOCTYPE html><html><script>var ytInitialData = '
    '{"contents":{"twoColumnSearchResultsRenderer":{"primaryContents":'
    '{"sectionListRenderer":{"contents":[{"itemSectionRenderer":{"contents":'
    '[{"videoRenderer":{"videoId":"BddP6PYo2gs","title":"Kesariya"}},'
    '{"videoRenderer":{"videoId":"zzzzzzzzzzz","title":"Another"}}]}}]}}}},'
    '"otherKeys":{"videoId":"zzzzzzzzzzz"}};</script></html>'
)


def serving(handler):
    """A real HTTP server on a port nobody else has."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            handler(self)

    server = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", server


@pytest.fixture
def watched(monkeypatch):
    """Records every address actually requested.

    Three tests here used to assert only that the answer was None -- and
    None is also what you get when the request is made and simply fails.
    So "it refused to fetch" and "it fetched and the network said no"
    looked identical, and the guards could be deleted without a test
    noticing.
    """
    tried = []
    real = httpx.AsyncClient.get

    async def watching(self, url, *a, **kw):
        tried.append(str(url))
        return await real(self, url, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "get", watching)
    return tried


@pytest.fixture
def reachable(monkeypatch):
    """Let it reach the stand-in, and nowhere else it should not."""
    real = fetch.check_address

    def narrowed(url):
        if url.startswith("http://127.0.0.1:"):
            return ["127.0.0.1"]
        return real(url)

    monkeypatch.setattr(fetch, "check_address", narrowed)


# --- finding the video -----------------------------------------------------

def test_the_first_result_is_the_one_he_asked_for():
    """The first id, not the most common one.

    YouTube repeats every id many times in its player configuration, so
    counting would pick whatever appears in the most markup rather than
    the top result.
    """
    assert play.first_video(RESULTS) == "BddP6PYo2gs"


def test_nothing_in_a_page_with_no_videos():
    for empty in ("", "<html>nothing here</html>", None):
        assert play.first_video(empty) is None


@pytest.mark.parametrize("nearly", [
    '"videoId":"tooshort"', '"videoId":"waaaaaaaaytoolong"',
    '"videoId":"has spaces"', '"videoId":"has/slash!!"', '"videoID":"BddP6PYo2gs"',
])
def test_only_a_real_video_id_matches(nearly):
    """Anything looser matches ids out of advertising blobs."""
    assert play.first_video(nearly) is None


# --- doing it end to end ---------------------------------------------------

@pytest.mark.asyncio
async def test_a_search_becomes_something_that_plays(reachable):
    base, server = serving(lambda h: (
        h.send_response(200), h.send_header("Content-Type", "text/html"),
        h.end_headers(), h.wfile.write(RESULTS.encode())))
    try:
        got = await play.playable("YouTube", f"{base}/results?search_query=kesariya",
                                  "kesariya")
    finally:
        server.shutdown()
    assert got == "https://www.youtube.com/watch?v=BddP6PYo2gs"


@pytest.mark.asyncio
async def test_opening_a_site_with_no_search_is_left_alone():
    """"open YouTube" is already right. Only "play X" needs this."""
    assert await play.playable("YouTube", "https://www.youtube.com", None) is None
    assert await play.playable("YouTube", "https://www.youtube.com", "") is None


@pytest.mark.asyncio
async def test_spotify_is_left_alone(watched):
    """Its web player needs a signed-in Premium account, so its search
    URL is already the best a link can do.

    Asserting nothing was FETCHED, not merely that nothing came back:
    reaching for open.spotify.com and failing would also return None.
    """
    assert await play.playable(
        "Spotify", "https://open.spotify.com/search/x", "x") is None
    assert watched == [], f"it went to the network anyway: {watched}"


# --- and never making it worse --------------------------------------------

@pytest.mark.asyncio
async def test_a_server_that_is_not_there_keeps_the_search_page(reachable):
    assert await play.playable(
        "YouTube", "http://127.0.0.1:1/results?search_query=x", "x") is None


@pytest.mark.asyncio
async def test_an_error_page_keeps_the_search_page(reachable):
    """The error page CARRIES a video id, which is the only way this
    test means anything.

    With a body of "busy" it passed whether or not the status was
    checked, because there was nothing to find either way. Real error
    pages carry markup, and one carrying something id-shaped would have
    been opened as if it were the song.
    """
    body = RESULTS.encode()
    base, server = serving(lambda h: (
        h.send_response(503), h.send_header("Content-Type", "text/html"),
        h.end_headers(), h.wfile.write(body)))
    try:
        assert await play.playable("YouTube", f"{base}/results", "x") is None, (
            "it took a video id out of a 503 page")
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_a_page_that_changed_shape_keeps_the_search_page(reachable):
    """YouTube will change its markup. When it does, this must go back to
    what it did before rather than failing the request."""
    base, server = serving(lambda h: (
        h.send_response(200), h.send_header("Content-Type", "text/html"),
        h.end_headers(), h.wfile.write(b"<html>redesigned, no ids</html>")))
    try:
        assert await play.playable("YouTube", f"{base}/results", "x") is None
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_it_will_not_fetch_an_address_on_your_own_network(watched):
    """The same check every other outbound fetch goes through.

    Asserting nothing was REQUESTED. Asserting only that the answer was
    None passed with the check deleted, because 192.168.1.1 is not
    reachable from a test machine either -- so the guard looked alive
    while being dead.
    """
    assert await play.playable(
        "YouTube", "http://192.168.1.1/results?search_query=x", "x") is None
    assert watched == [], f"it tried to reach the local network: {watched}"


def test_the_wait_is_short_enough_to_sit_through():
    """Stated as a number, because the test below patches it.

    The timing test has to shorten the timeout to run at all, which
    means it proves the timeout is OBEYED and says nothing about whether
    it is sane. A five-minute timeout would pass it.
    """
    assert play.TIMEOUT <= 5.0, (
        f"{play.TIMEOUT}s of silence between asking and the tab opening")


@pytest.mark.asyncio
async def test_a_slow_page_does_not_hold_up_the_reply(reachable, monkeypatch):
    """This runs between him asking and the tab opening. A second of
    silence is the whole budget."""
    import time

    monkeypatch.setattr(play, "TIMEOUT", 0.3)
    base, server = serving(lambda h: (
        time.sleep(2), h.send_response(200), h.end_headers(), h.wfile.write(b"x")))
    started = time.perf_counter()
    try:
        assert await play.playable("YouTube", f"{base}/results", "x") is None
    finally:
        server.shutdown()
    assert time.perf_counter() - started < 1.5, "it waited past its own timeout"
