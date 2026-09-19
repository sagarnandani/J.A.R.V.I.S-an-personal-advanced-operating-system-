"""Model provider keys, set from the dashboard rather than a text editor.

Adding a key used to mean an SSH session, an editor and a container
rebuild. The Render deployment this replaced asked for keys in a web
form, and that is the shape worth keeping -- not because typing in a
browser is better, but because it is the difference between a key being
added and a key being meant to be added later.

**Three rules this file exists to hold.**

*A key is never read back out.* There is no function here that returns
one to a caller outside the process, and no route that could. What can
be asked is whether one is set. A masked key is still most of a key.

*A key never reaches a model.* Nothing here is ever put in a prompt, and
the provider keys are not part of the context JARVIS is given about
itself.

*A key is never written to the repository.* That is the property that
makes this better than the file it replaces: a key in git is a key in
every clone, fork and backup, permanently, and deleting it later does
not take it back.

**Why a cache.** `get_provider` is called on every message and is
synchronous; reading the database there would mean an await in a hot
path that has none. So keys are loaded once at startup and refreshed
whenever one is written -- `cached()` is a dictionary lookup.
"""
import logging

from app.db import execute, fetch, fetchrow

logger = logging.getLogger("jarvis.keys")

PROVIDERS = ("gemini", "openai", "claude")

# How each is spelled to a person. `.title()` turns "openai" into
# "Openai", which looks like a typo in the one message he reads when
# something has gone wrong.
NAMES = {"gemini": "Gemini", "openai": "OpenAI", "claude": "Claude"}


def name_of(provider: str) -> str:
    return NAMES.get(provider, provider.title())

# provider -> (api_key, model or None). Populated at startup.
_CACHE: dict[str, tuple[str, str | None]] = {}


async def refresh() -> None:
    """Reload every stored key. Never raises.

    A deployment whose database is not reachable yet should start and
    fall back to environment variables, not refuse to boot over a
    convenience feature.
    """
    try:
        rows = await fetch("SELECT provider, api_key, model FROM provider_keys")
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not load provider keys: %s", exc)
        return

    _CACHE.clear()
    for row in rows:
        _CACHE[row["provider"]] = (row["api_key"], row["model"])
    if _CACHE:
        logger.info("Loaded provider key(s) for: %s",
                    ", ".join(sorted(_CACHE)))


def cached(provider: str) -> tuple[str | None, str | None]:
    """The stored key and model for a provider, from memory.

    Synchronous on purpose: this is called from the provider selection
    path, which runs on every message.
    """
    key, model = _CACHE.get(provider, (None, None))
    return key, model


async def store(provider: str, api_key: str, model: str | None,
                by: str) -> bool:
    """Save a key. Returns False for anything that is not a provider.

    The value is trimmed, because a key copied from a web page arrives
    with a newline on it more often than not, and a trailing newline
    produces an authentication failure that looks exactly like a wrong
    key.
    """
    provider = (provider or "").strip().lower()
    api_key = (api_key or "").strip()
    model = (model or "").strip() or None

    if provider not in PROVIDERS or not api_key:
        return False

    await execute(
        """
        INSERT INTO provider_keys (provider, api_key, model, set_by)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (provider) DO UPDATE
            SET api_key = EXCLUDED.api_key,
                model = EXCLUDED.model,
                set_by = EXCLUDED.set_by,
                updated_at = now()
        """,
        provider, api_key, model, by,
    )
    # Logged as an event, never with the value. "A key was set" is worth
    # having in the record; which key it was is not.
    logger.warning("A %s API key was set by %s.", provider, by)
    await refresh()
    return True


async def clear(provider: str, by: str) -> bool:
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS:
        return False
    result = await execute("DELETE FROM provider_keys WHERE provider = $1",
                           provider)
    logger.warning("The %s API key was removed by %s.", provider, by)
    await refresh()
    return result.endswith("1")


async def configured() -> dict:
    """Which providers have a key, and where it came from.

    Booleans and sources. Never a key, never a prefix, never a length --
    a length names the provider and a prefix is most of the secret.
    """
    from app.config import get_settings

    settings = get_settings()
    from_env = {
        "gemini": bool(settings.gemini_api_key),
        "openai": bool(settings.openai_api_key),
        "claude": bool(settings.anthropic_api_key),
    }

    rows = {}
    try:
        stored = await fetch(
            "SELECT provider, model, set_by, updated_at FROM provider_keys")
        rows = {r["provider"]: dict(r) for r in stored}
    except Exception:  # noqa: BLE001
        pass

    out = {}
    for name in PROVIDERS:
        here = name in rows
        out[name] = {
            "configured": here or from_env[name],
            # Which one is actually used, said plainly, because "I set it
            # in the dashboard but it is reading the old .env one" is an
            # hour of confusion otherwise.
            "source": "dashboard" if here else
                      ("environment" if from_env[name] else None),
            "model": (rows.get(name) or {}).get("model"),
            "set_by": (rows.get(name) or {}).get("set_by"),
            "updated_at": (rows.get(name) or {}).get("updated_at"),
        }
    return out


async def one_line() -> str:
    """A sentence for the dashboard and /health."""
    state = await configured()
    have = [name_of(n) for n, d in state.items() if d["configured"]]
    if not have:
        return ("No model provider is configured, so JARVIS returns "
                "clearly-labelled placeholder replies.")
    if len(have) == 1:
        return (f"{have[0]} only. The Auditor has to review work written "
                f"by the same model, and says so rather than implying the "
                f"review was independent.")
    return (f"{', '.join(have)}. The Auditor reviews a change on a "
            f"different model from the one that wrote it.")


async def probe(provider: str) -> dict:
    """Ask the provider whether the key actually works.

    A key that is saved and a key that works are different things, and
    the gap between them -- a typo, a revoked key, no billing set up --
    otherwise shows up much later as a failed message with a stack trace
    behind it.

    Deliberately the cheapest call each provider offers.
    """
    from app.config import get_settings
    from app.llm import _build

    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS:
        return {"ok": False, "why": "Not a provider JARVIS knows about."}

    built = _build(provider, get_settings())
    if built is None:
        return {"ok": False, "why": f"No {name_of(provider)} key is set."}

    try:
        result = await built.complete("Reply with the single word: ready")
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        return {"ok": False, "why": _explain(provider, text), "detail": text[:300]}

    return {"ok": True, "why": f"{name_of(provider)} answered.",
            "model": getattr(result, "model", None)}


def _explain(provider: str, error: str) -> str:
    """Turn a provider's error into the thing to do about it."""
    low = error.lower()
    if "api key" in low or "authentication" in low or "401" in low:
        return (f"{name_of(provider)} rejected the key. Check it was copied "
                f"whole, with no spaces.")
    if "quota" in low or "429" in low or "exhausted" in low or "rate" in low:
        return (f"The key works, but {name_of(provider)} is refusing calls: "
                f"quota or rate limit. Usually billing, or a free tier that "
                f"is used up.")
    if "model" in low and ("not found" in low or "does not exist" in low):
        return (f"The key works, but the model name does not. Set the model "
                f"beside the key.")
    if "billing" in low or "payment" in low or "credit" in low:
        return (f"{name_of(provider)} needs billing set up on the account "
                f"before the key will do anything.")
    return f"{name_of(provider)} could not be reached: {error[:160]}"
