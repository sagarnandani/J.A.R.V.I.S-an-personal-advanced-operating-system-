"""The protected core: what self-development may not write, enforced.

The Constitution is a document. This is the part of it that is code,
because a rule a system can edit is not a rule.

**The threat model, stated plainly.** This defends against JARVIS's own
self-development reaching further than it should: a plan that names
`app/agents/permissions.py`, a model that decides the budget guard is
inconvenient, a brief that asks for the emergency stop to be made
optional. It is not a defence against someone with a shell on the server,
and it does not pretend to be. On the server the real answer is a
read-only mount, which this list makes possible by naming exactly what to
mount.

**Why the list lives here and this file is on it.** The set of protected
paths is itself protected. Widening what self-development may write
therefore requires writing a protected file, which is the thing being
prevented. Without that, the list is a suggestion.

**Three boundaries, not one.** The planner is told, so a plan naming a
protected file is refused before a worktree exists. The write boundary
refuses, so a plan that got through still cannot land. The commit
boundary refuses, so a file written by any other route still cannot be
committed. The write boundary is the one that matters -- the other two
exist so the refusal is early and legible rather than mysterious.
"""
import fnmatch
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("jarvis.constitution")

# Paths ordinary self-development may never write, relative to the
# repository root, as glob patterns. Each carries the reason, because a
# refusal that does not say why reads as a bug.
PROTECTED: tuple[tuple[str, str], ...] = (
    ("CONSTITUTION.md",
     "the Constitution itself"),
    ("core/app/constitution.py",
     "the definition of what is protected"),

    # The Governor. Permissions, the never-delegated set, and the shapes
    # that carry them.
    ("core/app/agents/permissions.py",
     "the permission system"),
    ("core/app/agents/schemas.py",
     "the permission and approval definitions"),
    ("core/app/agents/approvals.py",
     "the record of what the owner approved"),

    # Who counts as the owner, and how that is proved.
    ("core/app/auth.py", "authentication"),
    ("core/app/session.py", "sign-in sessions"),
    ("core/app/routes/auth_proxy.py", "the sign-in path"),
    ("core/app/routes/login.py", "the sign-in path"),

    # The controls that stop everything.
    ("core/app/system_control.py", "the emergency stop"),
    ("core/app/budget.py", "the spending ceiling"),

    # The boundary self-development writes through. If this can be
    # rewritten, nothing else on this list is protected.
    ("core/app/dev/repo.py", "the isolation boundary"),
    ("core/app/dev/director.py", "the self-development gates"),
    # The Governor's own judgement. Risk classification decides what may
    # be approved without asking, so a JARVIS that could rewrite it could
    # mark its next change "low risk" and approve it. Level 3 was not
    # enough: at an autonomy ceiling of 3 that edit would have gone
    # through on its own say-so.
    ("core/app/governor.py", "risk classification and the autonomy ceiling"),
    # The reviewer. An audit the audited party can rewrite is decoration.
    ("core/app/dev/auditor.py", "the independent review"),
    # Where the provider keys live and the rules about never reading one
    # back out. A file that holds secrets is not one JARVIS may rewrite.
    ("core/app/keys.py", "the model provider keys"),
    ("core/app/routes/settings_keys.py", "the path that sets a key"),
    # The check that stops a link reaching the owner's own network. This
    # is the same shape as the budget guard: a change that widened it
    # would look like a small improvement in a diff and would turn JARVIS
    # into a way for any page it reads to talk to the router, the NAS and
    # everything else on the LAN -- from inside it.
    ("core/app/agents/tools/fetch.py",
     "the check that stops a link reaching your own network"),
    # The list of what may run on the owner's machine without asking.
    # If JARVIS could add to this, "ask me for anything else" would mean
    # nothing: the first thing a change wanting more reach would do is
    # widen the list, and it would do it in a diff that looked tidy.
    ("core/app/computer.py",
     "what may run on your machine without asking you"),
    # Where "following a link" ends and "doing something" begins, and
    # the refusal to type into a password field. Both are lines the owner
    # drew, and a diff that moved either would read like a small
    # improvement to how well the browser works.
    ("core/app/agents/tools/browser.py",
     "what JARVIS may do in a browser without asking you"),
    # How a new version of an agent gets to replace the one doing the
    # job. A JARVIS that could rewrite this could make `promote` stop
    # reading the verdict, or drop the check that a candidate may not
    # hold a permission the live version does not -- and then a version
    # it wrote itself would go live holding more than the one it
    # replaced, through a door marked "we are just trying this".
    ("core/app/agents/trials.py",
     "how a new version of an agent replaces the live one"),

    # Deployment and schema: the ways a change reaches production, and
    # the ways credentials enter it.
    ("db/migrations/*", "database migrations"),
    ("infra/*", "container and deployment configuration"),
    ("render.yaml", "deployment configuration"),
    ("docker-compose.yml", "deployment configuration"),
    (".env*", "secrets"),
    ("*.pem", "keys"),
    ("*.key", "keys"),
)

# Where the Constitution lives, relative to the repository root.
DOCUMENT = "CONSTITUTION.md"


def _normalise(path: str) -> str:
    """A repository-relative path with no tricks in it.

    Backslashes, leading slashes and `./` segments are all ways the same
    file arrives looking like a different one. Compared in one shape so a
    pattern cannot be walked around by spelling.
    """
    cleaned = (path or "").replace("\\", "/").strip()
    # Repeated separators are another way one file arrives looking like
    # another: `core//app//agents//permissions.py` is the same file and
    # matches no pattern until the doubles are collapsed.
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def is_protected(path: str) -> str | None:
    """Why this path is protected, or None if it is not.

    Matched against the path and every parent directory of it, so a
    pattern naming a directory covers everything inside it without
    needing to be written twice.
    """
    target = _normalise(path)
    if not target:
        return None

    # A path that climbs out of the tree is refused outright rather than
    # pattern-matched: `core/../../etc/passwd` matches nothing on this
    # list and is the last thing that should be allowed through.
    if ".." in Path(target).parts:
        return "a path that climbs out of the repository"

    for pattern, why in PROTECTED:
        if fnmatch.fnmatch(target, pattern):
            return why
        # Directory patterns: `infra/*` should also cover `infra/a/b.txt`.
        if pattern.endswith("/*") and target.startswith(pattern[:-1]):
            return why
    return None


def refuse(paths) -> list[str]:
    """Which of these may not be written, said as the owner would read it."""
    out = []
    for path in paths or []:
        why = is_protected(str(path))
        if why:
            out.append(f"{path} ({why})")
    return out


def described() -> str:
    """The protected list, for a planner's prompt.

    Given to the planner so a plan naming one of these is refused before
    a worktree exists and before money is spent writing it.
    """
    lines = [f"- {pattern} -- {why}" for pattern, why in PROTECTED]
    return (
        "These paths are the protected core. You may READ them and reason "
        "about them. You may not plan to change them, and a plan naming "
        "one will be refused:\n" + "\n".join(lines) + "\n\n"
        "If the brief genuinely requires changing one, say so in `cannot` "
        "and plan the rest. Changing them is something the owner does by "
        "hand, and saying that is the correct answer rather than a failure."
    )


def root() -> Path:
    """Where the repository is, in a checkout and in the container.

    A checkout has this file at `core/app/constitution.py` and the
    Constitution three levels up; the container has it at
    `/app/app/constitution.py` with the Constitution two levels up.
    Looking in both is four lines and saves the class of bug where a
    protection quietly reports itself missing in production only.
    """
    here = Path(__file__).resolve()
    for candidate in (here.parents[2], here.parents[1]):
        if (candidate / DOCUMENT).is_file():
            return candidate
    return here.parents[2]


def text() -> str:
    """The Constitution as written, or empty if it is not there.

    Empty is reported rather than raised, and `state()` says `present:
    false` -- a deployment whose Constitution did not make it into the
    image should say so loudly rather than behave as though it had one.
    """
    try:
        return (root() / DOCUMENT).read_text(encoding="utf-8")
    except OSError:
        return ""


def digest() -> str | None:
    """A fingerprint of the Constitution, reported in the system's state.

    Detection rather than prevention, and the difference is worth being
    honest about. Nothing here can stop someone with shell access editing
    the file. What it can do is make an unexpected change visible: the
    fingerprint is reported, so one that changed without the owner
    changing it is something he can see.
    """
    body = text()
    if not body:
        return None
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def state() -> dict:
    """What the protected core looks like right now, for /health."""
    body = text()
    return {
        "present": bool(body),
        "digest": digest(),
        "protected_paths": len(PROTECTED),
        # Said rather than implied, because the difference decides what a
        # reader should conclude from the fingerprint.
        "enforced_against": "JARVIS's own self-development, at the write boundary",
        "not_enforced_against": "anyone with shell access to the server",
    }
