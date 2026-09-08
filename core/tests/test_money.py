"""What came in and what went out.

The owner asked for this on day one. Most of what is tested is refusal:
a ledger that guesses is worse than none, because it looks like
arithmetic and gets checked against a bank statement that disagrees.
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app import money
from app.config import Settings


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute("DELETE FROM money_events; DELETE FROM memories;")
    yield db_pool
    await db_pool.execute("DELETE FROM money_events;")


# --- what becomes a row, and what does not --------------------------------

@pytest.mark.asyncio
async def test_a_stated_figure_becomes_a_row(clean):
    row = await money.record("in", "40000", "Bengaluru shoot", "client work")
    assert row["direction"] == "in"
    assert float(row["amount_inr"]) == 40000.0
    assert row["source"] == "stated"


@pytest.mark.parametrize("bad", [
    {"direction": "sideways", "amount_inr": 100, "what": "x"},
    {"direction": "in", "amount_inr": 0, "what": "x"},
    {"direction": "in", "amount_inr": -50, "what": "x"},
    {"direction": "in", "amount_inr": "lots", "what": "x"},
    {"direction": "in", "amount_inr": 100, "what": "   "},
])
@pytest.mark.asyncio
async def test_anything_that_is_not_a_real_figure_is_refused(clean, bad):
    """Refused, not repaired, and not raised.

    This runs in the background after a reply has gone out, so a
    malformed figure must cost a missing row rather than an error the
    owner cannot act on -- and certainly not a repaired row with a
    number nobody said.
    """
    assert await money.record(**bad) is None
    assert await clean.fetchval("SELECT count(*) FROM money_events") == 0


def test_the_extraction_pass_drops_everything_vague():
    """The model is asked for stated amounts; this is what enforces it."""
    kept = money.parse([
        {"direction": "in", "amount_inr": 40000, "what": "shoot"},
        {"direction": "out", "amount_inr": "5,000", "what": "drone battery"},
        {"direction": "in", "amount_inr": "a few thousand", "what": "vague"},
        {"direction": "in", "what": "no amount at all"},
        {"amount_inr": 100, "what": "no direction"},
        {"direction": "in", "amount_inr": 100},
        "not even a dict",
    ])
    assert [k["what"] for k in kept] == ["shoot", "drone battery"]
    assert kept[1]["amount_inr"] == Decimal("5000")


def test_a_comma_in_an_indian_figure_is_read_correctly():
    """Rs.1,50,000 is lakh notation, not a thousands separator error."""
    kept = money.parse([{"direction": "out", "amount_inr": "1,50,000", "what": "camera"}])
    assert kept[0]["amount_inr"] == Decimal("150000")


# --- totals ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_totals_are_sums_of_what_was_recorded(clean):
    await money.record("in", 40000, "shoot")
    await money.record("in", 10000, "retainer")
    await money.record("out", 5000, "battery")

    totals = await money.totals()
    assert totals["month_in"] == 50000.0
    assert totals["month_out"] == 5000.0
    assert totals["month_net"] == 45000.0
    assert totals["entries"] == 3


@pytest.mark.asyncio
async def test_last_months_money_is_not_this_months(clean):
    await money.record("in", 99999, "old job", occurred_on=date(2020, 1, 15))
    await money.record("in", 100, "today's job")

    totals = await money.totals()
    assert totals["month_in"] == 100.0, "an old figure leaked into this month"
    assert totals["entries"] == 2


# --- correcting it --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_misheard_figure_can_be_removed_outright(clean):
    """Deleted, not hidden, unlike a memory.

    A ledger that quietly keeps a number you told it to drop is one whose
    totals you cannot check against your own bank.
    """
    row = await money.record("in", 400000, "misheard forty thousand")
    assert await money.forget(row["id"]) is True
    assert await clean.fetchval("SELECT count(*) FROM money_events") == 0
    assert (await money.totals())["month_in"] == 0.0


@pytest.mark.asyncio
async def test_removing_something_that_is_not_there_says_so(clean):
    from uuid import uuid4

    assert await money.forget(uuid4()) is False


# --- through the exchange that already runs -------------------------------

@pytest.mark.asyncio
async def test_money_is_read_out_of_the_conversation(clean):
    """It rides on the fact-extraction call, so it costs nothing extra."""
    from app import facts
    from app.memory import store_memory

    said_in = await store_memory("got 40000 from the shoot", "episodic", "stated")

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            assert "money" in message.lower(), (
                "the extraction prompt never asks about money"
            )
            return SimpleNamespace(text='''{"facts": [], "supersedes": [],
                "money": [{"direction": "in", "amount_inr": 40000,
                           "what": "Bengaluru shoot", "category": "client work"}]}''',
                input_tokens=1, output_tokens=1, model="t", provider="mock")

    await facts.learn_from_exchange(Fake(), "got 40000 from the shoot",
                                    "Noted, sir.", said_in, 5)

    rows = await money.recent()
    assert len(rows) == 1
    assert float(rows[0]["amount_inr"]) == 40000.0
    assert rows[0]["said_in"] == said_in, (
        "the row does not link back to the words it was read out of"
    )


@pytest.mark.asyncio
async def test_a_broken_money_row_never_costs_the_facts(clean):
    """Fact learning must not fall over because a figure was malformed."""
    from app import facts
    from app.memory import store_memory

    said_in = await store_memory("something", "episodic", "stated")

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            return SimpleNamespace(text='''{"facts": [{"text": "Owner's wife is Sneha",
                "category": "people"}], "supersedes": [],
                "money": [{"direction": "maybe", "amount_inr": "some"}]}''',
                input_tokens=1, output_tokens=1, model="t", provider="mock")

    learned, _ = await facts.learn_from_exchange(Fake(), "x", "y", said_in, 5)
    assert learned == 1
    assert await clean.fetchval("SELECT count(*) FROM money_events") == 0


# --- what the briefing says -----------------------------------------------

@pytest.mark.asyncio
async def test_with_nothing_recorded_the_gap_is_named(clean):
    from app.status import briefing

    text = await briefing(Settings())
    assert "Money: nothing recorded yet" in text
    assert "never produce a figure" in text


@pytest.mark.asyncio
async def test_with_figures_recorded_it_reports_them_and_their_limits(clean):
    """The figures are real. What they are not is complete.

    "Rs.40,000 earned" read as a whole month's income would be a
    confident wrong answer about the one subject where that costs most,
    so the partialness has to survive into the prompt.
    """
    from app.status import briefing

    await money.record("in", 40000, "shoot")
    await money.record("out", 5000, "battery")

    text = await briefing(Settings())
    assert "Rs.40000.00 in" in text
    assert "Rs.5000.00 out" in text
    assert "net Rs.35000.00" in text
    assert "only what he has mentioned" in text
    assert "no bank or invoice feed exists" in text
