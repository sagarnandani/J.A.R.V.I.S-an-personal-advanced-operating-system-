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
from app.routes import admin, budget, health, message, records

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
    async with lifespan_db(app):
        logger.info("JARVIS Core started.")
        yield
    logger.info("JARVIS Core stopped.")


app = FastAPI(title="JARVIS Core", version="0.1.0-stage0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origins] if settings.cors_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(message.router)
app.include_router(records.router)
app.include_router(budget.router)
app.include_router(admin.router)

# The Stage 0 test console (plain HTML/JS) -- see /core/static/README for
# what this is and isn't. Mounted last so it doesn't shadow API routes.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
