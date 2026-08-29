"""JARVIS Core -- Stage 0.

A single FastAPI service: identity + memory-with-provenance + a working
request/response loop, per the Stage 0 Build Brief. No agents, no task
engine, no background workers -- those are later stages.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.auth import init_firebase
from app.config import get_settings
from app.db import lifespan_db
from app.routes import (
    admin,
    auth_proxy,
    budget,
    dashboard,
    health,
    login,
    message,
    records,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.dev_mode:
        logger.warning(
            "DEV_MODE is ON: authentication is bypassed and every caller is "
            "treated as the owner. This must NEVER be true on a deployment "
            "reachable from the internet."
        )
    init_firebase()
    _ensure_session_secret(settings)
    _warn_if_cookies_are_unprotected(settings)
    _warn_if_spend_is_untracked(settings)
    async with lifespan_db(app):
        logger.info("JARVIS Core started.")
        yield
    logger.info("JARVIS Core stopped.")


def _ensure_session_secret(settings) -> None:
    """Make sure login cookies can be signed, and say so if only just.

    Without a configured secret the server invents one at startup. Cookies
    signed with it are perfectly valid -- but the next restart invents a
    different one, and every cookie signed with the old one stops being
    recognised. In plain terms: it works, until the service restarts, and
    then you are signed out again. Since a redeploy IS a restart, that is
    the same "it keeps forgetting me" problem this cookie was added to
    fix, just moved. So it is a loud warning, not a quiet default.
    """
    import secrets

    if settings.session_secret:
        return

    settings.session_secret = secrets.token_urlsafe(32)
    logger.warning(
        "SESSION_SECRET is not set, so a temporary one was generated. Sign-ins "
        "will work, but every restart or redeploy will sign you out again. "
        "Set SESSION_SECRET in your environment -- see /docs/DEPLOYMENT.md step 4g."
    )


def _warn_if_cookies_are_unprotected(settings) -> None:
    """COOKIE_SECURE=false is correct on localhost and dangerous anywhere else.

    Without the Secure flag a browser will send the login cookie over
    plain http -- which is fine when "the network" is a loopback on your
    own machine, and means anyone on the path can lift your session
    anywhere else. It is a legitimate local setting and a serious mistake
    in the open, so it never passes silently.
    """
    if not settings.cookie_secure:
        logger.warning(
            "COOKIE_SECURE is false: the login cookie will be sent over plain "
            "HTTP. That is correct for http://localhost on your own machine "
            "and UNSAFE on anything reachable from another computer. Put "
            "JARVIS behind HTTPS and set COOKIE_SECURE=true before exposing it."
        )


def _warn_if_spend_is_untracked(settings) -> None:
    """Shout if the configured provider is priced at zero.

    A zero price is correct for Google's free tier, and wrong the moment
    billing gets enabled on that project -- at which point real money would
    be spent while the budget guard cheerfully reports Rs.0 used. That
    failure is silent and points the wrong way, so it gets a startup
    warning rather than a comment nobody reads.
    """
    from app.budget import provider_rates

    provider = settings.llm_provider.strip().lower()
    if provider == "mock":
        return
    price_in, price_out = provider_rates(provider, settings)
    if price_in == 0 and price_out == 0:
        logger.warning(
            "Provider '%s' is priced at 0, so /v1/budget will report zero spend "
            "no matter how much is used. That is correct ONLY while it is on a "
            "genuinely free tier. If billing is enabled for it, set its price "
            "environment variables -- see /docs/BUDGET.md.",
            provider,
        )


app = FastAPI(title="JARVIS Core", version="0.1.0-stage0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origins] if settings.cors_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth_proxy.router)
app.include_router(login.router)
app.include_router(message.router)
app.include_router(records.router)
app.include_router(budget.router)
app.include_router(dashboard.router)
app.include_router(admin.router)

# The Stage 0 test console (plain HTML/JS) -- see /core/static/README for
# what this is and isn't. Mounted last so it doesn't shadow API routes.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
