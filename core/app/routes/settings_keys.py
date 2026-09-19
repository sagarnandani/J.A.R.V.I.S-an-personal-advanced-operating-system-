"""Setting a model provider key from the dashboard.

The whole point of this file is that the key goes in and never comes
back out. There is no endpoint here that returns one, and there is no
parameter that would make one return one.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import keys, system_control
from app.auth import CurrentUser, get_current_user

router = APIRouter()
logger = logging.getLogger("jarvis.routes.keys")


class KeyIn(BaseModel):
    provider: str
    api_key: str
    # Optional: only when the default model for that provider is not the
    # one wanted. Left blank for almost everybody.
    model: str | None = None


@router.get("/v1/settings/providers", include_in_schema=False)
async def which(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Which providers are set, where from, and what that means.

    Booleans and sources. Never a key.
    """
    return {"providers": await keys.configured(), "said": await keys.one_line()}


@router.post("/v1/settings/providers", include_in_schema=False)
async def save(
    body: KeyIn, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Store a key, then immediately try it.

    Saving and working are different things, and the gap between them --
    a truncated paste, a revoked key, no billing on the account -- would
    otherwise surface much later as a failed message with a stack trace
    behind it. So it is tested here, while he is still looking at the
    box he typed it into.
    """
    await system_control.refuse_if_stopped()

    who = f"user:{user.email or user.uid}"
    if not await keys.store(body.provider, body.api_key, body.model, who):
        raise HTTPException(
            status_code=400,
            detail=(f"'{body.provider}' is not a provider JARVIS knows "
                    f"about, or the key was empty. Known: "
                    f"{', '.join(keys.PROVIDERS)}."),
        )

    checked = await keys.probe(body.provider)
    return {
        "saved": True,
        "works": checked["ok"],
        "said": checked["why"],
        "providers": await keys.configured(),
        "summary": await keys.one_line(),
    }


@router.post("/v1/settings/providers/{provider}/test", include_in_schema=False)
async def test(
    provider: str, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Try a key that is already stored. One cheap call."""
    await system_control.refuse_if_stopped()
    return await keys.probe(provider)


@router.delete("/v1/settings/providers/{provider}", include_in_schema=False)
async def remove(
    provider: str, user: CurrentUser = Depends(get_current_user)
) -> dict:
    """Forget a key.

    Note what this does not do: it does not touch the environment. If a
    key is also in .env, removing the stored one falls back to that, and
    `source` will say so rather than reporting the provider gone.
    """
    who = f"user:{user.email or user.uid}"
    removed = await keys.clear(provider, who)
    return {"removed": removed, "providers": await keys.configured(),
            "summary": await keys.one_line()}
