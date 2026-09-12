"""Handing JARVIS something to read.

Upload, and JARVIS reads it. What it does not do is act on it: the file
becomes material for the next message, and anything it asks for goes
through the same offer the owner approves by hand. See app/attachments.py
for why that boundary is where it is.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import attachments
from app.auth import CurrentUser, get_current_user

router = APIRouter()
logger = logging.getLogger("jarvis.routes.attachments")


@router.post("/v1/attachments", include_in_schema=False)
async def upload(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Read a file and keep what it said.

    Refusals are said in words, not as a status code the page has to
    translate. "JARVIS cannot read that" and "that PDF is scanned images"
    are different problems with different fixes, and the owner needs to
    know which one he has.
    """
    data = await file.read()
    try:
        row = await attachments.store(
            file.filename or "attachment", file.content_type or "",
            data, f"user:{user.email or user.uid}",
        )
    except attachments.Unreadable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not store an attachment")
        raise HTTPException(
            status_code=500, detail=f"That file could not be stored: {exc}"
        ) from exc

    return {
        "id": str(row["id"]),
        "filename": row["filename"],
        "chars": row["chars"],
        "pages": row["pages"],
        "bytes": row["bytes"],
        "already_had_it": row.get("already_had_it", False),
        # Said rather than implied. An attachment that has been read and
        # not acted on is the normal state, and the page says so.
        "note": (
            "Read and kept. It has not been acted on: send a message and "
            "JARVIS will tell you what it says and what it could do."
        ),
    }


@router.get("/v1/attachments", include_in_schema=False)
async def listing(
    limit: int = 20, user: CurrentUser = Depends(get_current_user)
) -> list[dict]:
    return await attachments.recent(limit)


@router.get("/v1/attachments/{attachment_id}", include_in_schema=False)
async def one(
    attachment_id: UUID, user: CurrentUser = Depends(get_current_user)
) -> dict:
    row = await attachments.get(attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such attachment.")
    row["id"] = str(row["id"])
    return row
