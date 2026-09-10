"""The engine that has always been speaking: the device's own synthesiser.

It produces no audio on the server. The browser holds the voices, so this
engine hands back settings and the page makes the sound -- which is a real
engine and not a stand-in, because it is what speaks today, needs no key,
costs nothing and works on an iPad with no server at all.

Its ceiling should be said plainly. These are the system voices Apple and
Google ship. The best British male among them is competent and dated, and
no amount of configuration will make it sound like the brief's premium
cinematic voice. What this engine can do is get the accent, the pace and
the delivery right, and be replaced without anything above it changing.
"""
from app.voice import profile as vp
from app.voice.engine import Spoken, register

# A system voice at rate 1.0 speaks somewhere around 170 words per minute.
# That is an approximation and it differs per voice and per platform, so
# it lives here as a named constant rather than as a magic number, and the
# client measures nothing -- it simply cannot. Treat the resulting rate as
# close, not exact.
ASSUMED_WPM_AT_RATE_1 = 170.0

# Web Speech clamps rate to 0.1-10 and pitch to 0-2. Values outside that
# are ignored silently by some browsers and throw in others, so they are
# clamped here rather than discovered on a device.
RATE_RANGE = (0.5, 2.0)
PITCH_RANGE = (0.5, 1.5)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class BrowserEngine:
    name = "browser"
    # It cannot begin before the text exists, and the text arrives whole.
    streams = False
    # The sound is made on the device, not here.
    synthesises = False

    def available(self, settings) -> bool:
        """Always. That is the point of it being the fallback."""
        return True

    def configure(self, voice: vp.VoiceProfile, delivery: vp.Delivery) -> dict:
        """The profile in Web Speech's own terms.

        Pitch is a multiplier on a voice whose natural pitch is already
        whatever it is, so "medium-low" becomes a nudge downwards rather
        than an absolute. Pushing it further makes the voice sound
        processed, which the brief rules out explicitly.
        """
        base_pitch = {"low": 0.85, "medium-low": 0.92,
                      "medium": 1.0, "high": 1.1}.get(voice.pitch, 0.92)
        rate = (voice.words_per_minute / ASSUMED_WPM_AT_RATE_1) * delivery.rate
        return {
            "rate": round(_clamp(rate, *RATE_RANGE), 3),
            "pitch": round(_clamp(base_pitch * delivery.pitch, *PITCH_RANGE), 3),
            "volume": 1.0,
            "lang": "en-GB",
            "prefer": list(voice.prefer.get("browser", ())),
            "delivery": delivery.key,
        }

    async def say(self, text: str, voice: vp.VoiceProfile,
                  delivery: vp.Delivery) -> Spoken:
        return Spoken(
            text=text, engine=self.name, delivery=delivery.key,
            settings=self.configure(voice, delivery),
        )


register(BrowserEngine())
