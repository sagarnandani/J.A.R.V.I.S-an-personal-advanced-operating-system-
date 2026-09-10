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
    agents,
    auth_proxy,
    budget,
    dashboard,
    health,
    live,
    login,
    media,
    message,
    records,
    voice,
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
        # Schema first: the registry and the task tables have to exist
        # before anything tries to use them.
        await _apply_migrations(settings)
        # Inside the pool: the registry lives in the database, so this
        # cannot run before there is a connection to it.
        await _install_agents()
        ticker = _start_scheduler(settings)
        logger.info("JARVIS Core started.")
        try:
            yield
        finally:
            if ticker is not None:
                ticker.cancel()
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


async def _apply_migrations(settings) -> None:
    """Bring the database up to date with the code that is deploying.

    The owner has no terminal. Making them find a SQL editor for every
    schema change is friction JARVIS can simply remove, and a deploy that
    silently needs a manual step is a deploy that will be forgotten.
    """
    if not settings.auto_migrate:
        logger.info("AUTO_MIGRATE is off; not checking the schema.")
        return
    try:
        from app.db import get_pool
        from app.migrate import apply_pending

        await apply_pending(get_pool())
    except Exception as exc:  # noqa: BLE001 - never worth refusing to start
        logger.error("Could not check or apply migrations: %s", exc)


def _start_scheduler(settings):
    """Check for due work while the process is awake.

    Only half the story on a free tier, where the process sleeps: a loop
    that is not running notices nothing. POST /v1/cron/tick is the other
    half, for something outside to wake it. Both call the same code, and
    an advisory lock means it does not matter if they overlap.
    """
    if not settings.scheduler_enabled:
        logger.info("Scheduled work is switched off (SCHEDULER_ENABLED).")
        return None
    import asyncio

    from app import scheduler

    logger.info("Scheduler running; checking every %ds.", scheduler.TICK_SECONDS)
    return asyncio.create_task(scheduler.loop(settings))


async def _install_agents() -> None:
    """Register the built-in capabilities at startup.

    Needs the database, so it runs inside the lifespan rather than at
    import. A failure here leaves JARVIS entirely usable -- the agent
    foundation is additional machinery, not something conversation
    depends on -- so it warns instead of refusing to start.
    """
    from app.agents import builtin

    try:
        await builtin.install()
        logger.info("Agent foundation ready.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not install built-in agents: %s", exc)


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


# Interactive docs only in DEV_MODE.
#
# Every endpoint behind them needs the owner's cookie, so listing them is
# not a way in -- but a public page advertising /v1/memories/forget-all
# and /v1/memories/purge on a personal system is surface with no purpose.
# It also names the version, which is a free hint to anyone looking.
_dev = get_settings().dev_mode
app = FastAPI(
    title="JARVIS Core", version="0.1.0-stage0", lifespan=lifespan,
    docs_url="/docs" if _dev else None,
    redoc_url="/redoc" if _dev else None,
    openapi_url="/openapi.json" if _dev else None,
)

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
app.include_router(live.router)
app.include_router(admin.router)
app.include_router(agents.router)
app.include_router(media.router)
app.include_router(voice.router)

# The Stage 0 test console (plain HTML/JS) -- see /core/static/README for
# what this is and isn't. Mounted last so it doesn't shadow API routes.
class RevalidatingStatic(StaticFiles):
    """Static files that are always checked before being reused.

    Without a Cache-Control header a browser invents its own freshness
    window, and Safari's is generous -- generous enough that an iPad ran
    the new index.html against a cached app.js from before the Tasks tab
    existed. The tab was there, the code behind it was not, and tapping it
    did nothing.

    `no-cache` does not mean "do not store". It means "store, but ask
    before reusing", so a browser still gets a 304 and no body when
    nothing has changed. For an app whose whole front end is three small
    files, one conditional request per load is nothing next to shipping a
    deploy that only half arrives.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", RevalidatingStatic(directory="static", html=True), name="static")
