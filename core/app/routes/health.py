"""Public, unauthenticated health check -- "how do I know if it's running"."""
from fastapi import APIRouter, Request

from app import constitution, system_control
from app.config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    stopped = await system_control.is_stopped()
    return {
        "status": "ok",
        "emergency_stop": stopped,
        "dev_mode": get_settings().dev_mode,
        # Which code is running and whether the database matches it. Both
        # questions have now cost real time to answer from a phone, and
        # both are one query away from the thing already being asked.
        "schema": await _schema(),
        # The protected core. Reported rather than only enforced, so a
        # Constitution that changed without the owner changing it is
        # something he can see. Nothing secret is in here: the paths are
        # in the repository and the fingerprint is of a public file.
        "constitution": constitution.state(),
        # Can JARVIS build changes to itself here, and if not, why not?
        #
        # This shipped without an answer and cost a weekend. Inside a
        # container the image is a copy of the source with no .git in it,
        # so the Build tab rendered completely and did nothing -- and the
        # only way to find that out was to read the code. It is one line
        # of JSON and it is public, because the shape of a deployment is
        # not a secret and needing to sign in to ask "is it working" is
        # exactly backwards.
        "self_development": await _build_readiness(),
        # Which build is actually running. "I pulled and rebuilt" and "the
        # new code is running" are different statements, and until now
        # there was no way to tell them apart from outside.
        "running": _running_build(),
        # Which model providers have a key. Whether, never what.
        #
        # "I added the key" and "the key reached the container" are two
        # different statements, and checking used to mean either trusting
        # the first or printing an environment dump -- which is how a key
        # ends up pasted into a chat window.
        "providers": _providers(),
    }


def _providers() -> dict:
    """Booleans only. No key, no prefix, not even a length.

    A masked key is still a key with most of it visible, and a length
    tells you which provider it is from. There is nothing useful here
    that is not a yes or a no.
    """
    from app.llm import CLAUDE, GEMINI, OPENAI

    settings = get_settings()
    configured = {
        GEMINI: bool(settings.gemini_api_key),
        OPENAI: bool(settings.openai_api_key),
        CLAUDE: bool(settings.anthropic_api_key),
    }
    have = [name for name, ready in configured.items() if ready]
    return {
        "configured": configured,
        "default": settings.llm_provider,
        "independent_review": len(have) >= 2,
        "means": (
            "No model provider is configured, so JARVIS returns clearly "
            "labelled placeholder replies."
            if not have else
            f"{', '.join(n.title() for n in have)} configured. "
            + ("The Auditor can review a change on a different model from "
               "the one that wrote it."
               if len(have) >= 2 else
               "With only one, the Auditor reviews work written by the "
               "same model and reports that the review is not independent.")
        ),
    }


def _running_build() -> dict:
    """The commit this process is running, if it can be known.

    Best effort and never fatal. An image built from a copy of the source
    genuinely does not know, and saying so is better than guessing.
    """
    import os

    info = {"commit": None, "how": "unknown"}
    try:
        from app.dev import repo

        where = repo.root(get_settings())
        head = where / ".git" / "HEAD"
        if head.exists():
            ref = head.read_text().strip()
            if ref.startswith("ref: "):
                target = where / ".git" / ref[5:]
                if target.exists():
                    info = {"commit": target.read_text().strip()[:12],
                            "branch": ref[5:].rsplit("/", 1)[-1],
                            "how": "read from the mounted repository"}
            else:
                info = {"commit": ref[:12], "how": "detached head"}
    except Exception:  # noqa: BLE001
        pass

    # Set this in the build if you want the image to carry its own stamp.
    stamped = os.environ.get("BUILD_COMMIT")
    if stamped:
        info["image_built_from"] = stamped[:12]
    return info


def _fix_for(state: dict) -> str | None:
    """The remedy for THIS reason, not a general one.

    A diagnostic that always prints the same fix is worse than printing
    none: the first time it is right it teaches you to trust it, and the
    second time it sends you to edit docker-compose over a file you
    forgot to commit.
    """
    if state["usable"]:
        return None
    why = (state.get("why") or "").lower()
    if "not a git repository" in why:
        return ("The container has no git history -- the image is a copy of "
                "the source and .dockerignore excludes .git. docker-compose "
                "must mount the repository (./:/repo), set REPO_PATH=/repo, "
                "and run as a user that can write it.")
    if "uncommitted changes" in why:
        return ("Commit or stash your own edits in that checkout. This is "
                "not a misconfiguration -- it stops a proposed change from "
                "containing work JARVIS did not write.")
    if "git is not installed" in why:
        return "Install git in the image, or run JARVIS where git exists."
    return "Read `why_not`: it is the specific reason, not a category."


async def _build_readiness() -> dict:
    """Whether the Build tab will do anything, said before it is opened."""
    try:
        from app.dev import repo

        state = await repo.state(get_settings())
        return {
            "usable": state["usable"],
            "why_not": state["why"] or None,
            "repo_path": state["path"],
            "branch": state["branch"],
            "fix": _fix_for(state),
        }
    except Exception as exc:  # noqa: BLE001
        return {"usable": False, "why_not": f"could not be checked: {exc}"}


async def _schema() -> dict:
    """Never the reason /health fails.

    A health check that goes down because a diagnostic went down is worse
    than no diagnostic: it turns a working deployment into one that looks
    dead.
    """
    try:
        from app.db import get_pool
        from app.migrate import state

        return await state(get_pool())
    except Exception:  # noqa: BLE001
        return {"state": "unknown"}


@router.get("/public/firebase-config")
async def firebase_config(request: Request) -> dict:
    """Public Firebase web config for the Stage 0 test console.

    These values identify the Firebase project to the client SDK; they are
    not secrets, the same way a website's domain name isn't a secret.

    `authDomain` is deliberately reported as *this* server's own host
    rather than the project's firebaseapp.com address. Sign-in only
    completes if the app and Firebase's sign-in helper share a domain --
    Safari blocks the cross-domain storage the split version depends on --
    and app/routes/auth_proxy.py serves that helper from here to make them
    match. Deriving it from the incoming request means this keeps working
    wherever JARVIS is deployed, with nothing to reconfigure.
    """
    settings = get_settings()
    host = request.headers.get("host") or settings.firebase_auth_domain
    return {
        "apiKey": settings.firebase_api_key,
        "authDomain": host,
        "projectId": settings.firebase_project_id,
        "appId": settings.firebase_app_id,
        "googleClientId": settings.google_client_id,
    }
