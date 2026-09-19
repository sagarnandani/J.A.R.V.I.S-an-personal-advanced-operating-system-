"""Provider keys set from the dashboard.

Adding a key used to mean an SSH session, an editor and a rebuild. The
point of this is that it is a box on a page instead.

Most of these tests are about the one thing that must never happen: a
key coming back out. There is no function that returns one outside the
process and no route that could, and that is asserted rather than
assumed -- a leak here is permanent in a way most bugs are not.
"""
import pytest
import pytest_asyncio

from app import keys

SECRET = "sk-test-do-not-print-this-value-1234567890"


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute("DELETE FROM provider_keys")
    keys._CACHE.clear()
    yield db_pool
    await db_pool.execute("DELETE FROM provider_keys")
    keys._CACHE.clear()


# --- it works --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_key_is_stored_and_reaches_the_provider_selection(clean):
    assert await keys.store("openai", SECRET, None, "user:sagar") is True
    stored, model = keys.cached("openai")
    assert stored == SECRET
    assert model is None


@pytest.mark.asyncio
async def test_a_key_pasted_with_a_newline_still_works(clean):
    """A key copied from a web page arrives with whitespace on it more
    often than not, and a trailing newline fails authentication in a way
    that looks exactly like a wrong key."""
    await keys.store("openai", f"  {SECRET}\n", None, "user:sagar")
    assert keys.cached("openai")[0] == SECRET


@pytest.mark.asyncio
async def test_setting_a_key_twice_replaces_it(clean):
    await keys.store("openai", "first", None, "user:sagar")
    await keys.store("openai", "second", None, "user:sagar")
    assert keys.cached("openai")[0] == "second"


@pytest.mark.asyncio
async def test_a_model_can_be_stored_beside_the_key(clean):
    await keys.store("openai", SECRET, "gpt-test", "user:sagar")
    assert keys.cached("openai") == (SECRET, "gpt-test")


@pytest.mark.asyncio
async def test_removing_a_key_forgets_it(clean):
    await keys.store("openai", SECRET, None, "user:sagar")
    assert await keys.clear("openai", "user:sagar") is True
    assert keys.cached("openai") == (None, None)


@pytest.mark.asyncio
async def test_nonsense_is_refused_rather_than_stored(clean):
    assert await keys.store("skynet", SECRET, None, "x") is False
    assert await keys.store("openai", "", None, "x") is False
    assert await keys.store("openai", "   ", None, "x") is False


@pytest.mark.asyncio
async def test_the_database_refuses_a_provider_nobody_knows(clean, db_pool):
    """Two independent refusals: the function checks, and so does the
    column, so removing one does not open the other."""
    import asyncpg

    with pytest.raises(asyncpg.CheckViolationError):
        await db_pool.execute(
            "INSERT INTO provider_keys (provider, api_key) VALUES ('skynet','x')")


# --- the key never comes back ----------------------------------------------

@pytest.mark.asyncio
async def test_nothing_reported_to_the_page_contains_the_key(clean):
    await keys.store("openai", SECRET, None, "user:sagar")

    for reported in (await keys.configured(), await keys.one_line()):
        printed = repr(reported)
        assert SECRET not in printed

        # Any recognisable run of the key, not just the whole thing --
        # half a key is still half a key.
        for start in range(0, len(SECRET) - 8):
            assert SECRET[start:start + 8] not in printed, (
                f"part of the key leaked: {SECRET[start:start + 8]!r}")

    # The length must not be REPORTED, because a length narrows down
    # which provider a key is from. Checked as "no field equals it"
    # rather than "the digits appear nowhere in the output" -- the
    # output carries a timestamp, and the naive version failed whenever
    # the clock happened to contain those two digits. A test that fails
    # on the minute of the hour is one you learn to rerun rather than
    # read.
    def values(obj):
        if isinstance(obj, dict):
            for item in obj.values():
                yield from values(item)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                yield from values(item)
        else:
            yield obj

    for value in values(await keys.configured()):
        assert value != len(SECRET), "the key's length is reported"
        assert value != str(len(SECRET)), "the key's length is reported"


@pytest.mark.asyncio
async def test_it_says_where_the_key_came_from(clean):
    """"I set it in the dashboard but it is still using the old one" is
    an hour of confusion that one word prevents."""
    await keys.store("openai", SECRET, None, "user:sagar")
    state = await keys.configured()
    assert state["openai"]["source"] == "dashboard"
    assert state["gemini"]["source"] is None


def test_no_function_here_returns_a_key_to_the_outside():
    """`cached` is the only reader, and it is called from inside the
    provider selection. Everything a route can reach returns booleans."""
    import inspect

    public = [name for name, value in vars(keys).items()
              if inspect.isfunction(value) and not name.startswith("_")]
    assert "cached" in public
    # The route module may only call these.
    from app.routes import settings_keys

    source = inspect.getsource(settings_keys)
    assert "cached(" not in source, (
        "the route layer reads a key value; it must only ever ask whether "
        "one is set"
    )
    assert "api_key" not in source.split("class KeyIn")[1].split("@router")[1], (
        "a key value is referenced after the input model -- check it is "
        "not being returned"
    )


def test_the_key_store_is_protected_from_self_development():
    """A file holding secrets is not one JARVIS may rewrite."""
    from app import constitution

    assert constitution.is_protected("core/app/keys.py")
    assert constitution.is_protected("core/app/routes/settings_keys.py")


# --- telling him what is wrong ---------------------------------------------

@pytest.mark.parametrize("error,expected", [
    ("Error code: 401 - invalid api key", "rejected the key"),
    ("authentication failed", "rejected the key"),
    ("429 RESOURCE_EXHAUSTED", "quota or rate limit"),
    ("You exceeded your current quota", "quota or rate limit"),
    ("The model `gpt-old` does not exist", "model name does not"),
    ("Please set up billing to continue", "billing set up"),
])
def test_a_provider_error_becomes_the_thing_to_do_about_it(error, expected):
    assert expected in keys._explain("openai", error)


def test_openai_is_spelled_the_way_it_is_spelled():
    """`.title()` gives "Openai", which reads as a typo in the one
    message he sees when something has gone wrong."""
    assert keys.name_of("openai") == "OpenAI"
    assert keys.name_of("gemini") == "Gemini"
