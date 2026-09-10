"""The guard that catches JARVIS saying it did something it did not do.

Three reports in one week, all the same failure. It said it was
displaying a script on screen. It said a piece was waiting on the Media
tab. It said it was fetching the latest information, then answered from
training data that had never heard of the thing being asked about.

Every one was a model that knows a capability exists, cannot reach it
from a conversation, and describes using it. The prompt is told not to.
This is what catches it when the prompt does not, so the tests care about
two things above all: that it fires on the sentences that actually
reached the owner, and that it stays silent on ordinary replies. A guard
that cries wolf gets ignored, and then it is worse than nothing.
"""
import pytest

from app import claims


# --- the sentences that actually reached the owner -------------------------

@pytest.mark.parametrize("said", [
    "I'm looking that up now, sir.",
    "I am fetching the latest information on that.",
    "I've written the script; it is available in the Media tab.",
    "I have prepared the post for you.",
    "Let me check the current figures.",
    "Give me a minute while I search.",
    "One moment, sir.",
    "The draft is on the Media tab whenever you want it.",
    "I'm displaying it on your screen now.",
    "I've posted it for you.",
])
def test_a_claim_with_nothing_behind_it_is_corrected(said):
    corrected, why = claims.correct(said, offered=False)
    assert why is not None, f"not caught: {said!r}"
    assert claims.CORRECTION in corrected
    # Additive only. A guard that rewrote replies would eventually mangle
    # a good one and there would be no way to tell from outside.
    assert corrected.startswith(said)


# --- and the ones that must be left alone ---------------------------------

@pytest.mark.parametrize("said", [
    "The subsidy was 15 percent as of 2024, though that may have changed.",
    "I can look that up properly if you would like.",
    "I could check that against live sources, sir.",
    "I have three unfinished priorities noted for you.",
    "Rs.1,200 of the Rs.3,500 ceiling has gone this month.",
    "Sneha's birthday is on the fourth.",
    "That would need looking up; my own knowledge stops before it.",
    "You asked me to check it last week and I told you then.",
    "",
])
def test_an_ordinary_reply_is_left_exactly_as_it_was(said):
    corrected, why = claims.correct(said, offered=False)
    assert why is None, f"fired on an ordinary reply: {said!r}"
    assert corrected == said


def test_a_claim_with_an_offer_behind_it_is_true_and_left_alone():
    """With a card on screen, "I'll look that up" is a statement about a
    button the owner is looking at."""
    said = "I'm looking that up now, sir."
    corrected, why = claims.correct(said, offered=True)
    assert why is None
    assert corrected == said


def test_the_guard_never_raises():
    for odd in [None, "", "\x00", "[[", "I'm " * 5000]:
        corrected, why = claims.correct(odd, offered=False)
        assert corrected == odd or isinstance(corrected, str)


# --- a marked offer nothing can run --------------------------------------

def test_a_dropped_offer_is_said_out_loud_not_only_logged():
    """The owner had just read a sentence saying work was coming, saw no
    card, and had nothing on screen telling them why."""
    said = "I can put that together properly, sir."

    for kind in ("look_up", "make"):
        out = claims.nothing_registered(said, kind)
        assert out.startswith(said)
        assert "nothing has started" in out

    made = claims.nothing_registered(said, "make")
    assert "Media tab" in made, (
        "the owner is not told that the Media tab will stay empty"
    )


def test_an_unknown_kind_still_says_something():
    out = claims.nothing_registered("Right.", "teleport")
    assert "nothing has started" in out
