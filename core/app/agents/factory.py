"""Making a specialist when one is needed, and unmaking it after.

Section 40B. The registry has always held agents; nothing could add one
while JARVIS was running. So "research the Karnataka EV subsidy rules
specifically" had to be squeezed into a general researcher, or wait for
me to write a new module and a deploy.

**The one rule this file exists to enforce.**

    A child may never hold what its parent does not.

Not "should not". The permissions are intersected with the parent's
before the row is written, so a request for more comes back with less
rather than being refused and retried until something slips. And no
agent may ever be delegated MODIFY_CONFIG or MODIFY_AGENTS, so the
capability to make agents cannot itself be handed to a made agent --
otherwise the first thing a sufficiently motivated chain would do is
build a child that can build children, and the ceiling would be one
recursion away from gone.

**Temporary unless there is a reason.** A specialist made for one job is
EXPERIMENTAL and swept when it stops being used. The brief is explicit
about not accumulating dozens of permanently running agents, and a
registry full of one-off agents nobody remembers making is how an
organisation chart stops meaning anything.

**It is the Governor's to allow.** Creating an agent is a change to what
JARVIS can do, which is structural learning, not behaviour. So it goes
through the same approval as anything else at that risk level -- this
module refuses and records; it does not decide.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.agents import registry
from app.agents.schemas import (
    NEVER_DELEGATED,
    AgentSpec,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.db import execute, fetch

logger = logging.getLogger("jarvis.agents.factory")

# A made agent's name has to be safe to put in a URL, a log line and a
# capability lookup. Anything else is refused rather than sanitised:
# quietly renaming what somebody asked for is how two agents end up
# sharing a name.
NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

# Where made agents live. Never a bare name: "research" made at runtime
# and "research" shipped in the code must not be able to collide, and
# the prefix is how anyone reading the registry can tell which is which.
DOMAIN = "made"

# How long a temporary specialist is kept after its last use. Long
# enough to be reused within a session; short enough that the registry
# does not silt up.
KEEP_HOURS = 24

# What a made agent may never hold, on top of the never-delegated set.
# Making agents is the one that matters: a factory that can make
# factories has no ceiling.
NEVER_MADE = NEVER_DELEGATED | {Permission.SENSITIVE}


class CannotMake(Exception):
    """The agent will not be made, and this says why in plain words."""


def _clean_name(name: str) -> str:
    wanted = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not NAME.fullmatch(wanted):
        raise CannotMake(
            f"{name!r} is not a usable agent name. Use three to forty "
            f"letters, digits or underscores, starting with a letter.")
    return wanted


def narrow(asked, parent: AgentSpec | None) -> frozenset[Permission]:
    """What the child actually gets: what it asked for, bounded.

    Intersected rather than refused. A request for more comes back with
    less, so a caller that asks for too much gets a working agent that
    can do less -- rather than an error it can retry with a slightly
    smaller ask until something slips through.
    """
    wanted = set()
    for name in asked or ():
        try:
            wanted.add(Permission(str(name).strip().lower()))
        except ValueError:
            raise CannotMake(
                f"There is no permission called {name!r}. The ones that "
                f"exist: {', '.join(sorted(p.value for p in Permission))}."
            ) from None

    wanted -= NEVER_MADE
    if parent is not None:
        wanted &= set(parent.permissions)
    return frozenset(wanted)


async def make(
    name: str,
    purpose: str,
    *,
    parent: AgentSpec | None = None,
    permissions=(),
    task_types=("general",),
    tiers=(ModelTier.CHEAP, ModelTier.STANDARD),
    persist: bool = False,
    by: str,
    max_cost_inr: Decimal | None = None,
) -> AgentSpec:
    """Register one specialist. Returns what it actually got.

    `parent` is the agent asking. Passing None means the owner asked
    directly, which is the only way an agent gets permissions nobody
    already held -- and even then, never the ones in NEVER_MADE.
    """
    capability = f"{DOMAIN}.{_clean_name(name)}"
    if not (purpose or "").strip():
        raise CannotMake(
            "Say what it is for. An agent nobody can describe is one "
            "nobody can decide about later.")

    granted = narrow(permissions, parent)
    dropped = sorted(
        p.value for p in
        (narrow(permissions, None) - granted)) if parent else []

    existing = await registry.get(capability)
    if existing is not None and existing.status is not Lifecycle.RETIRED:
        raise CannotMake(
            f"There is already an agent called {capability!r}. Retire it "
            f"first, or pick another name.")

    spec = AgentSpec(
        capability=capability,
        name=purpose.strip()[:80],
        description=purpose.strip(),
        domain=DOMAIN,
        supervisor=parent.capability if parent else None,
        task_types=tuple(task_types) or ("general",),
        permissions=granted,
        model_tiers=tuple(tiers),
        # Made agents are never ACTIVE on arrival. TESTING is routable
        # only when asked for by name, which is exactly what a specialist
        # made for one job should be.
        status=Lifecycle.ACTIVE if persist else Lifecycle.TESTING,
        max_cost_inr=max_cost_inr,
        config={"scopes": ["working"], "made_by": by,
                "made_at": datetime.now(timezone.utc).isoformat(),
                "temporary": not persist},
    )
    await registry.register(spec)

    logger.info("Made %s for %s (%s)%s.", capability, by,
                ", ".join(sorted(p.value for p in granted)) or "no permissions",
                f"; refused {', '.join(dropped)}" if dropped else "")
    await _audit(capability, by, granted, dropped)
    return spec


async def retire(capability: str, *, by: str) -> bool:
    """Stand a made agent down. Only ever one this module made.

    A factory that could retire the shipped agents would be a way to
    disable the Governor's own reviewers by asking politely.
    """
    if not capability.startswith(f"{DOMAIN}."):
        raise CannotMake(
            f"{capability!r} was not made at runtime, so this cannot retire "
            f"it. Only agents under '{DOMAIN}.' belong to the factory.")
    done = await execute(
        "UPDATE agents SET status = 'retired', updated_at = now() "
        "WHERE capability = $1 AND status <> 'retired'", capability)
    if done.endswith("1"):
        logger.info("Retired %s at %s's request.", capability, by)
    return done.endswith("1")


async def sweep() -> list[str]:
    """Retire temporary specialists nobody has used lately.

    The brief is explicit about not accumulating dozens of agents. A
    registry full of one-off specialists nobody remembers making is how
    an organisation chart stops meaning anything.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=KEEP_HOURS)
    rows = await fetch(
        """
        SELECT a.capability FROM agents a
         WHERE a.domain = $1 AND a.status <> 'retired'
           AND (a.config->>'temporary')::boolean IS TRUE
           AND a.updated_at < $2
           AND NOT EXISTS (
               SELECT 1 FROM tasks t
                WHERE t.capability = a.capability AND t.created_at > $2)
        """,
        DOMAIN, cutoff,
    )
    swept = []
    for row in rows:
        if await retire(row["capability"], by="sweep"):
            swept.append(row["capability"])
    if swept:
        logger.info("Swept %d unused specialist(s).", len(swept))
    return swept


async def made() -> list[dict]:
    """Every agent the factory built, for the org chart and the owner."""
    rows = await fetch(
        "SELECT capability, name, description, status, permissions, "
        "supervisor, config, created_at FROM agents WHERE domain = $1 "
        "ORDER BY created_at DESC", DOMAIN)
    return [{
        "capability": r["capability"], "purpose": r["description"],
        "status": r["status"], "permissions": sorted(r["permissions"] or []),
        "parent": r["supervisor"] or "you",
        "temporary": bool((r["config"] or {}).get("temporary")),
        "made_by": (r["config"] or {}).get("made_by", ""),
        "made_at": r["created_at"],
    } for r in rows]


async def _audit(capability: str, by: str, granted, dropped) -> None:
    from app.audit import log_audit

    try:
        await log_audit(
            actor=by,
            action=(f"made agent {capability} with "
                    f"{', '.join(sorted(p.value for p in granted)) or 'no permissions'}"
                    + (f"; refused {', '.join(dropped)}" if dropped else "")),
            # Changing what JARVIS can do is structural, never routine.
            category="high_risk",
            outcome="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not record an agent being made: %s", exc)
