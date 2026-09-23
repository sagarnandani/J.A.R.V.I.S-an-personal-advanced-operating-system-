"""Posting to LinkedIn, through LinkedIn's own API.

**Why not just drive a logged-in browser.** It would work, and it is the
wrong answer for three reasons the owner should hear once. LinkedIn's
User Agreement forbids automated access, and the account it would put at
risk is his real professional identity -- a restriction there costs him
something JARVIS cannot give back. It would need a browser permanently
signed in as him, which is exactly the thing the sidecar was designed
never to be. And it would break every time LinkedIn moved a button,
silently, in a way that looks like JARVIS being unreliable rather than
like a selector going stale.

So: the official API. It needs a one-off setup from him -- an app on
LinkedIn's developer site and one authorisation -- and after that it is
stable, allowed, and survives redesigns.

**What is stored and why it cannot be hashed.** Unlike a password, an
access token has to be REPLAYED to be useful, so it is kept as it is.
The mitigation is that it never appears in a log, never leaves through
any route, and the only thing that reads it is the function that posts.

**Nothing here decides to post.** It is called by a capability holding
PUBLISH, which the runtime stops for approval, because PUBLISH is in
ALWAYS_APPROVED. Posting without asking is possible and it is a thing
the owner turns on deliberately, per section 20 of the brief -- an
explicitly configured policy, not a default and not something an agent
can arrange for itself.
"""
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.db import execute, fetchrow

logger = logging.getLogger("jarvis.social.linkedin")

PLATFORM = "linkedin"

AUTHORIZE = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO = "https://api.linkedin.com/v2/userinfo"
# The current posts API. `ugcPosts` below is the older one, still live
# and still what some apps are approved for -- which one an app may use
# depends on the products LinkedIn granted it, and that is not knowable
# from here. So both, newest first, and the error says which to fix.
POSTS = "https://api.linkedin.com/rest/posts"
UGC_POSTS = "https://api.linkedin.com/v2/ugcPosts"

# What to ask for. `w_member_social` is the one that permits posting;
# the other two are how the account's own id is discovered, which is
# needed because a post must name its author as a URN.
SCOPES = ("openid", "profile", "w_member_social")

# LinkedIn requires a dated version on the newer API and rejects one it
# does not recognise. A setting rather than a constant, because it moves
# and a stale one must be changeable without a deploy.
DEFAULT_VERSION = "202405"

TIMEOUT = 20.0

# A post that is too long is rejected by LinkedIn with a message nobody
# reads. Caught here, where it can say what to do.
MAX_LENGTH = 3000


class NotConnected(Exception):
    """No LinkedIn account is connected. Says how to connect one."""


class PostFailed(Exception):
    """The post did not go out, and this says what LinkedIn said."""


@dataclass(frozen=True)
class Account:
    urn: str
    name: str
    scopes: tuple[str, ...]
    expires_at: datetime | None
    connected_at: datetime
    last_post_at: datetime | None

    @property
    def may_post(self) -> bool:
        return "w_member_social" in self.scopes

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= datetime.now(timezone.utc))

    def as_detail(self) -> dict:
        """Never includes the token. There is no route that returns it."""
        return {
            "platform": PLATFORM, "name": self.name, "urn": self.urn,
            "scopes": list(self.scopes), "may_post": self.may_post,
            "expired": self.expired, "expires_at": self.expires_at,
            "connected_at": self.connected_at, "last_post_at": self.last_post_at,
        }


# --- connecting ------------------------------------------------------------

async def start(redirect_uri: str, settings, *, by: str) -> str:
    """The consent URL to send the owner to.

    The `state` is stored and single-use: without it, anything that could
    reach the callback could hand JARVIS a token for an account nobody
    asked to connect.
    """
    if not settings.linkedin_client_id:
        raise NotConnected(
            "There is no LinkedIn app configured. Create one at "
            "developers.linkedin.com, add the 'Sign In with LinkedIn using "
            "OpenID Connect' and 'Share on LinkedIn' products, then set "
            "LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET on the server.")

    state = secrets.token_urlsafe(24)
    await execute(
        "INSERT INTO social_oauth_states (state, platform, started_by) "
        "VALUES ($1,$2,$3)", state, PLATFORM, by)

    from urllib.parse import urlencode

    return AUTHORIZE + "?" + urlencode({
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(SCOPES),
    })


async def finish(code: str, state: str, redirect_uri: str, settings, *,
                 by: str) -> Account:
    """Swap the code for a token, find out whose account it is, store it."""
    row = await fetchrow(
        "UPDATE social_oauth_states SET used_at = now() WHERE state = $1 "
        "AND platform = $2 AND used_at IS NULL AND expires_at > now() "
        "RETURNING state", (state or "").strip(), PLATFORM)
    if row is None:
        raise NotConnected(
            "That sign-in did not come from JARVIS, or it took longer than "
            "ten minutes. Start again from the dashboard.")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        answer = await client.post(TOKEN, data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.linkedin_client_id,
            "client_secret": settings.linkedin_client_secret,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if answer.status_code >= 400:
            raise NotConnected(_why(answer, "LinkedIn refused the sign-in"))
        token = answer.json()

        access = token.get("access_token")
        if not access:
            raise NotConnected("LinkedIn returned no access token.")

        who = await client.get(USERINFO,
                               headers={"Authorization": f"Bearer {access}"})
        if who.status_code >= 400:
            raise NotConnected(_why(
                who, "Signed in, but LinkedIn would not say whose account "
                     "it is. The app probably lacks the 'Sign In with "
                     "LinkedIn using OpenID Connect' product"))
        person = who.json()

    sub = person.get("sub")
    if not sub:
        raise NotConnected("LinkedIn did not return an account id.")

    granted = tuple((token.get("scope") or "").replace(",", " ").split())
    expires = (datetime.now(timezone.utc)
               + timedelta(seconds=int(token.get("expires_in") or 0))
               if token.get("expires_in") else None)

    await execute(
        """
        INSERT INTO social_accounts (platform, account_urn, account_name,
                                     access_token, refresh_token, expires_at,
                                     scopes, connected_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (platform) DO UPDATE SET
            account_urn = EXCLUDED.account_urn,
            account_name = EXCLUDED.account_name,
            access_token = EXCLUDED.access_token,
            refresh_token = EXCLUDED.refresh_token,
            expires_at = EXCLUDED.expires_at,
            scopes = EXCLUDED.scopes,
            connected_at = now(),
            connected_by = EXCLUDED.connected_by
        """,
        PLATFORM, f"urn:li:person:{sub}", person.get("name") or "",
        access, token.get("refresh_token"), expires,
        list(granted or SCOPES), by,
    )
    logger.info("LinkedIn connected as %s by %s.", person.get("name"), by)
    found = await account()
    assert found is not None
    return found


async def account() -> Account | None:
    row = await fetchrow("SELECT * FROM social_accounts WHERE platform = $1",
                         PLATFORM)
    if row is None:
        return None
    return Account(
        urn=row["account_urn"], name=row["account_name"],
        scopes=tuple(row["scopes"] or ()), expires_at=row["expires_at"],
        connected_at=row["connected_at"], last_post_at=row["last_post_at"],
    )


async def disconnect() -> bool:
    done = await execute("DELETE FROM social_accounts WHERE platform = $1",
                         PLATFORM)
    return done.endswith("1")


# --- posting ---------------------------------------------------------------

async def post(text: str, settings, *, visibility: str = "PUBLIC") -> dict:
    """Put one piece of text on the owner's feed. Returns evidence.

    Evidence, not a claim: the id LinkedIn gave it and a link to it. A
    function that returned True would be asking to be believed, and
    section 33 of the brief exists because "I posted it" is exactly the
    sentence that must never be taken on trust.
    """
    text = (text or "").strip()
    if not text:
        raise PostFailed("There is nothing to post.")
    if len(text) > MAX_LENGTH:
        raise PostFailed(
            f"That is {len(text):,} characters and LinkedIn takes "
            f"{MAX_LENGTH:,}. Shorten it by {len(text) - MAX_LENGTH:,}.")

    row = await fetchrow(
        "SELECT * FROM social_accounts WHERE platform = $1", PLATFORM)
    if row is None:
        raise NotConnected(
            "No LinkedIn account is connected. Connect one on the Build tab.")

    who = await account()
    if who and not who.may_post:
        raise NotConnected(
            "The connected LinkedIn account did not grant posting. Add the "
            "'Share on LinkedIn' product to your app and connect again.")
    if who and who.expired:
        raise NotConnected(
            "The LinkedIn connection has expired. Connect it again on the "
            "Build tab.")

    token, urn = row["access_token"], row["account_urn"]
    version = getattr(settings, "linkedin_api_version", "") or DEFAULT_VERSION

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # The current API first.
        answer = await client.post(POSTS, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
        }, json={
            "author": urn,
            "commentary": text,
            "visibility": visibility,
            "distribution": {"feedDistribution": "MAIN_FEED",
                             "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        })
        posted_id = answer.headers.get("x-restli-id") or answer.headers.get("x-linkedin-id")

        if answer.status_code >= 400:
            # The older endpoint, for an app approved for that one
            # instead. Which an app may use depends on the products
            # LinkedIn granted it, and that is not knowable from here.
            logger.info("LinkedIn /rest/posts said %s; trying ugcPosts.",
                        answer.status_code)
            older = await client.post(UGC_POSTS, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            }, json={
                "author": urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": visibility},
            })
            if older.status_code >= 400:
                raise PostFailed(_why(older, "LinkedIn would not take the post"))
            posted_id = (older.json() or {}).get("id") or posted_id

    await execute(
        "UPDATE social_accounts SET last_post_at = now() WHERE platform = $1",
        PLATFORM)
    logger.info("Posted to LinkedIn as %s.", urn)
    return {
        "platform": PLATFORM,
        "id": posted_id or "",
        "url": (f"https://www.linkedin.com/feed/update/{posted_id}"
                if posted_id else ""),
        "characters": len(text),
    }


def _why(response, lead: str) -> str:
    """LinkedIn's own words, trimmed, with the status that caused them.

    Its errors are the useful part -- "unpermitted fields", "not enough
    permissions" and an expired token all look identical from outside and
    need completely different fixes.
    """
    body = ""
    try:
        said = response.json()
        body = said.get("message") or said.get("error_description") or str(said)
    except Exception:  # noqa: BLE001
        body = (response.text or "")[:300]
    return f"{lead} ({response.status_code}): {body[:400]}"


async def state(settings) -> dict:
    """For the dashboard, and for "can you post for me?"."""
    who = await account()
    configured = bool(getattr(settings, "linkedin_client_id", ""))
    return {
        "configured": configured,
        "connected": who is not None,
        "account": who.as_detail() if who else None,
        "said": (
            "No LinkedIn app is set up on the server yet."
            if not configured else
            "A LinkedIn app is set up, but no account is connected."
            if who is None else
            f"Connected as {who.name or 'your account'}."
            + ("" if who.may_post else
               " It cannot post: the 'Share on LinkedIn' product is missing.")
            + (" The connection has expired." if who.expired else "")
        ),
        "how": (
            "Create an app at developers.linkedin.com, add the 'Sign In with "
            "LinkedIn using OpenID Connect' and 'Share on LinkedIn' products, "
            "and set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET."
            if not configured else ""),
    }
