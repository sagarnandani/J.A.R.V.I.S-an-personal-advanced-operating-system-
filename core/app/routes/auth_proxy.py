"""Serve Firebase's sign-in helper from our own domain.

The problem this solves, in order:

  * Signing in with a pop-up window doesn't work on iPhone/iPad -- Safari
    blocks pop-ups by default, so the window never opens.
  * The usual alternative, redirecting the whole page, normally fails too.
    Firebase's sign-in helper is hosted at <project>.firebaseapp.com, a
    different domain from wherever the app runs. Completing sign-in needs
    the two domains to share browser storage, and Safari (and increasingly
    everyone else) blocks exactly that.

Google's own guidance is to serve that helper from the same domain as the
app. On Firebase Hosting that's a setting; anywhere else -- Render, a home
server -- it means forwarding those few paths ourselves, which is what
this does.

So `https://your-jarvis/__/auth/...` quietly relays to
`https://<project>.firebaseapp.com/__/auth/...`, the browser only ever
sees one domain, and nothing is cross-origin.

See https://firebase.google.com/docs/auth/web/redirect-best-practices
"""
import logging

import httpx
from fastapi import APIRouter, Request, Response

from app.config import get_settings

logger = logging.getLogger("jarvis.auth_proxy")

router = APIRouter()

# Headers that describe one hop of a connection rather than the message,
# so they must not be copied through a proxy.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length", "content-encoding",
}


def upstream_url(project_id: str, path: str, query: str) -> str:
    """Where a given /__/auth/... request should really go."""
    url = f"https://{project_id}.firebaseapp.com/__/auth/{path}"
    return f"{url}?{query}" if query else url


def rewrite_set_cookie(value: str) -> str:
    """Make a cookie set by Firebase's domain apply to ours instead.

    Firebase sends cookies scoped to firebaseapp.com. Passed through
    unchanged, the browser rejects them -- they don't match the domain it
    is actually talking to. Dropping the Domain attribute makes each
    cookie apply to whatever host served it, which is the point of
    proxying in the first place.
    """
    parts = [p for p in value.split(";") if not p.strip().lower().startswith("domain=")]
    return ";".join(parts)


def forwardable_request_headers(headers) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


@router.api_route(
    "/__/auth/{path:path}", methods=["GET", "POST", "OPTIONS"], include_in_schema=False
)
async def firebase_auth_proxy(path: str, request: Request) -> Response:
    settings = get_settings()
    if not settings.firebase_project_id:
        return Response(
            content="Firebase is not configured on this server.",
            status_code=503,
            media_type="text/plain",
        )

    target = upstream_url(settings.firebase_project_id, path, request.url.query)
    body = await request.body()

    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
            upstream = await client.request(
                request.method,
                target,
                content=body or None,
                headers=forwardable_request_headers(request.headers),
            )
    except httpx.HTTPError as exc:
        # Reaching Firebase failed outright -- network, DNS, timeout, or an
        # egress policy in the way. Say so, rather than letting it surface
        # as a bare "Internal Server Error" that suggests JARVIS is broken
        # when the actual problem is that this server cannot get out.
        logger.warning("Could not reach Firebase's sign-in helper: %s", exc)
        return Response(
            content=(
                "JARVIS could not reach Google to sign you in. This server "
                "was unable to open a connection to Firebase, so it is a "
                "network problem rather than a wrong password or account. "
                f"({type(exc).__name__})"
            ),
            status_code=502,
            media_type="text/plain",
        )

    out = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
    # Redirects must be passed on so the browser follows them to Google.
    if "location" in upstream.headers:
        out.headers["location"] = upstream.headers["location"]
    for cookie in upstream.headers.get_list("set-cookie"):
        out.headers.append("set-cookie", rewrite_set_cookie(cookie))
    return out
