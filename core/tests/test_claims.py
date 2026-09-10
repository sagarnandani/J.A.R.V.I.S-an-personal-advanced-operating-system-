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


# --- the third person, and the invented excuse -----------------------------
#
# The first version of this list caught the first person, so the model
# moved to the third. Asked why a script was slow, it said the work was in
# process and that some topics take longer to research. Neither sentence
# contains the word "I", and both were about work that was not happening.

@pytest.mark.parametrize("said", [
    "It's in process, sir.",
    "The script is being prepared.",
    "That research is under way.",
    "Still working on it.",
    "It should be ready shortly.",
    "It's almost ready.",
    "Some topics take longer to research.",
    "This one takes more time than usual.",
])
def test_work_described_without_saying_who_is_doing_it_is_still_a_claim(said):
    corrected, why = claims.correct(said, offered=False)
    assert why is not None, f"not caught: {said!r}"
    assert claims.CORRECTION in corrected


def test_the_guard_stays_quiet_when_something_really_is_running():
    """Once the status notes carry work in progress, "it is still being
    researched" is a report rather than a fabrication -- and a guard that
    corrected it would be the one telling the untruth."""
    said = "It's still being researched, sir."

    caught, why = claims.correct(said, offered=False, in_flight=False)
    assert why is not None and claims.CORRECTION in caught

    left, why = claims.correct(said, offered=False, in_flight=True)
    assert why is None
    assert left == said


# --- naming the machinery, which is the thing they all had in common -------
#
# Enumerating phrasings turned into whack-a-mole. The guard caught the
# first person, so the model used the third. It caught "in progress", so
# the model said the Media Director was working on it. What every one of
# those sentences has in common is that it names a part of the system and
# says that part is doing something.

@pytest.mark.parametrize("said", [
    "The Media Director is working on it now.",
    "The chain is running; the script will follow.",
    "Your workflow has started, sir.",
    "media.script is drafting it.",
    "The agents are busy with that.",
])
def test_saying_a_part_of_the_system_is_at_work_is_a_claim(said):
    corrected, why = claims.correct(said, offered=False)
    assert why is not None, f"not caught: {said!r}"
    assert claims.CORRECTION in corrected


@pytest.mark.parametrize("said", [
    "The scout, the strategist and the reviewer are the media agents.",
    "The Media Director is a recipe rather than an agent.",
    "You have three unfinished priorities today.",
    "There are nine capabilities registered.",
    "I can put that together properly if you like.",
])
def test_naming_the_machinery_without_claiming_it_is_busy_is_fine(said):
    """The owner asks what agents exist. Naming them is not a claim."""
    corrected, why = claims.correct(said, offered=False)
    assert why is None, f"fired on an ordinary reply: {said!r}"
    assert corrected == said


# --- the fact under the claim ---------------------------------------------

def test_the_true_state_goes_under_a_reply_about_the_machinery():
    """A fact rather than a judgement. Deciding whether a sentence is a
    lie is unreliable; saying what is running is never wrong."""
    state = "Nothing is running, and no piece of content has ever been started."

    said = claims.with_state("The Media Director is working on it.", state)
    assert said.endswith(state)
    assert said.startswith("The Media Director is working on it.")

    # Not bolted onto every reply, only the ones that talk about it.
    plain = "Sneha's birthday is on the fourth."
    assert claims.with_state(plain, state) == plain
    assert claims.with_state("", state) == ""
    assert claims.with_state("The chain is running.", "") == "The chain is running."


def test_the_fact_attaches_even_where_the_correction_cannot_reach():
    """"The reviewer is going over it" is in no verb list and never will
    be -- enumerating them was whack-a-mole. The fact underneath it still
    says nothing is running."""
    state = "Nothing is running."
    said = "The reviewer is going over it."

    _, why = claims.correct(said, offered=False)
    assert why is None, "the correction is deliberately stricter than this"
    assert claims.with_state(said, state).endswith(state)
