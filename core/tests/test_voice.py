"""JARVIS_VOICE_V1 and the engine seam it plugs into.

Two things are being protected here. That the voice is configured in one
place, so it cannot drift back into being decided in four. And that the
engine is genuinely replaceable, which is only true if nothing above it
knows which engine is speaking -- so the tests register a fake one and
check that everything above carries on unchanged.

The sentence splitting gets the most attention because it is the part
that is heard. A wrong split is not a subtle bug: the voice stops
mid-thought and starts again, and it sounds like a fault.
"""
import pytest

from app import voice
from app.config import Settings
from app.voice import engine as ve
from app.voice import profile as vp


@pytest.fixture
def clean_engines():
    """Put the registry back afterwards, so one test cannot mute another."""
    saved = dict(ve._ENGINES)
    yield
    ve._ENGINES.clear()
    ve._ENGINES.update(saved)


# --- one place, not four ---------------------------------------------------

def test_the_profile_is_the_only_place_the_voice_is_described():
    p = vp.JARVIS_VOICE_V1
    assert p.name == "JARVIS_VOICE_V1"
    assert p.accent == "British" and p.gender == "male"
    assert 135 <= p.words_per_minute <= 145, "the brief's pace"
    assert p.narration_style is False and p.robotic_effect is False


def test_the_description_carries_what_it_must_not_sound_like():
    """An engine that designs a voice from text needs the negatives.

    They are the half of a voice brief most easily lost, and the half
    that stops it drifting into a movie trailer.
    """
    said = vp.JARVIS_VOICE_V1.described().lower()
    for banned in ("trailer", "announcer", "narration", "monotone", "robotic"):
        assert banned in said, f"{banned} is not ruled out"


def test_an_unknown_delivery_speaks_normally_rather_than_not_at_all():
    assert vp.delivery("nonsense").key == "normal"
    assert vp.delivery(None).key == "normal"
    assert vp.delivery("WARNING").key == "warning"


def test_the_six_deliveries_are_variations_not_performances():
    """Large departures are how a controlled voice starts acting."""
    for key, d in vp.DELIVERIES.items():
        assert 0.9 <= d.rate <= 1.1, f"{key} changes pace too much"
        assert 0.9 <= d.pitch <= 1.1, f"{key} changes pitch too much"


def test_deliveries_actually_differ_from_one_another():
    """Six names that all sound the same would be decoration."""
    shapes = {(d.rate, d.pitch) for d in vp.DELIVERIES.values()}
    assert len(shapes) >= 4, "the deliveries are not distinguishable"


# --- the browser engine ----------------------------------------------------

def test_the_browser_engine_is_always_available():
    """It is the fallback. A fallback that can be unavailable is not one."""
    assert voice.select(Settings()).name == "browser"


def test_the_profile_becomes_web_speech_settings():
    engine = voice.select(Settings())
    conf = engine.configure(vp.JARVIS_VOICE_V1, vp.delivery("normal"))

    assert conf["lang"] == "en-GB"
    assert 0.5 <= conf["rate"] <= 2.0, "outside what Web Speech accepts"
    assert 0.5 <= conf["pitch"] <= 1.5
    assert conf["prefer"], "no named voices to prefer"


def test_a_slower_delivery_produces_a_slower_rate():
    engine = voice.select(Settings())
    normal = engine.configure(vp.JARVIS_VOICE_V1, vp.delivery("normal"))
    analysis = engine.configure(vp.JARVIS_VOICE_V1, vp.delivery("analysis"))
    warning = engine.configure(vp.JARVIS_VOICE_V1, vp.delivery("warning"))

    assert analysis["rate"] < normal["rate"]
    assert warning["pitch"] < normal["pitch"]


def test_an_absurd_profile_still_produces_settings_a_browser_accepts():
    """Clamped here rather than discovered on a device.

    Some browsers ignore an out-of-range rate silently and others throw.
    Either way the owner hears nothing and there is nothing on screen.
    """
    engine = voice.select(Settings())
    fast = vp.VoiceProfile(words_per_minute=900)
    conf = engine.configure(fast, vp.delivery("normal"))
    assert conf["rate"] <= 2.0


@pytest.mark.asyncio
async def test_the_browser_engine_returns_instructions_not_audio():
    """It is a real engine; the sound is simply made on the device."""
    said = await voice.say("All primary systems are operational.", Settings())
    assert said.engine == "browser"
    assert said.audio is None
    assert said.settings["rate"] > 0


# --- the seam --------------------------------------------------------------

class FakeEngine:
    name = "fake"
    streams = True
    synthesises = True

    def __init__(self, ok=True):
        self.ok = ok

    def available(self, settings):
        return self.ok

    def configure(self, voice_profile, delivery):
        return {"voice": voice_profile.name, "mode": delivery.key}

    async def say(self, text, voice_profile, delivery):
        return ve.Spoken(text=text, engine=self.name, delivery=delivery.key,
                         settings=self.configure(voice_profile, delivery),
                         audio=b"audio", mime="audio/wav")


@pytest.mark.asyncio
async def test_a_new_engine_needs_no_change_above_it(clean_engines):
    """The whole point of the seam, as a test."""
    ve.register(FakeEngine())
    said = await voice.say("Good evening.", Settings(voice_engine="fake"))

    assert said.engine == "fake"
    assert said.audio == b"audio"


def test_an_engine_that_cannot_run_falls_back_rather_than_failing(clean_engines):
    """An owner whose neural voice has fallen over should hear a plainer
    JARVIS, not silence."""
    ve.register(FakeEngine(ok=False))
    assert voice.select(Settings(voice_engine="fake")).name == "browser"


def test_a_configured_engine_that_does_not_exist_falls_back(clean_engines):
    assert voice.select(Settings(voice_engine="elevenlabs")).name == "browser"


def test_a_better_engine_is_preferred_over_the_browser_by_default(clean_engines):
    """Empty configuration means "whatever is available", and the browser
    is deliberately last because it is the one that always works."""
    ve.register(FakeEngine())
    assert voice.select(Settings(voice_engine="")).name == "fake"


# --- what is actually heard ------------------------------------------------

def test_a_reply_is_split_into_sentences():
    lines = ve.chunks(
        "Good evening, Sagar. All primary systems are operational. "
        "You have three unfinished priorities today."
    )
    assert len(lines) == 3
    assert lines[0] == "Good evening, Sagar."


@pytest.mark.parametrize("text", [
    "Rs.1,200 was spent this month against a Rs.3,500 ceiling, so nothing needs doing.",
    "Dr. Rao replied about the meeting, and the answer was yes, it is confirmed.",
    "The figure is 3.5 percent, which is lower than the estimate we were given.",
])
def test_a_full_stop_inside_a_figure_or_a_title_is_not_a_sentence_end(text):
    """The failure is audible: JARVIS stops after "Rs" and starts again."""
    assert ve.chunks(text) == [text]


def test_a_fragment_too_short_to_stand_alone_is_joined_up():
    """A voice that stops after one word sounds broken rather than brief."""
    lines = ve.chunks("Yes. The analysis is complete and the result is promising.")
    assert lines[0].startswith("Yes."), "the fragment was dropped"
    assert len(lines) == 1


def test_a_very_long_sentence_is_cut_at_a_comma_never_mid_word():
    long_one = (
        "The analysis is complete and the result is promising, although "
        "there are two risks worth examining before we proceed, the first "
        "being the cost of the third-party service, and the second being "
        "the time it will take to migrate everything across to the new "
        "server this coming weekend."
    )
    lines = ve.chunks(long_one, max_chars=120)
    assert len(lines) > 1
    assert all(len(l) <= 130 for l in lines)
    for line in lines[:-1]:
        assert line.endswith(","), f"cut somewhere other than a comma: {line!r}"
    # Nothing may be lost. A voice that silently drops the end of a
    # sentence is worse than one that reads it slowly.
    assert "".join(lines).replace(" ", "") == long_one.replace(" ", "")


def test_nothing_to_say_produces_nothing():
    assert ve.chunks("") == []
    assert ve.chunks("   \n  ") == []


def test_every_word_survives_the_split():
    original = (
        "Your meeting begins in twenty minutes. At your current pace, "
        "arriving on time would be an unexpected achievement, sir."
    )
    assert " ".join(ve.chunks(original)) == original
