"""Eyes and hands on the owner's other machines.

Sections 19 and 40C. The server is the brain: it runs all the time, it
holds the memory and the Governor, and it has no screen, no browser of
the owner's, and no access to his Mac's files. A sidecar is a small
program he runs on one of those machines that offers a **named, limited**
set of things it will do on JARVIS's behalf.

**The sidecar connects out. JARVIS never dials into his laptop.**

That is the most important decision in this file and it is not a
convenience. Nothing has to be forwarded through his router; no port is
opened on a machine that also holds his email; it works from a cafe; and
the machine that decides whether to be reachable is the machine itself.
Closing the laptop is the off switch, and there is no way to override
that from here, because there is nothing here that reaches out.

**Three narrowings, and a job has to survive all of them.**

    what the sidecar was granted   (the owner, at pairing)
  ∩ what the agent holds           (its registry entry)
  ∩ what the action needs          (this one job)

A Mac granted screenshots is not a Mac granted a terminal, even though
the same program could do both. An agent holding READ_FILES cannot use a
sidecar's terminal. And the intersection is computed here, once, in
`may_run` -- not re-derived at each call site, because a permission
check that exists in four places is a permission check that will exist
in three places after the next refactor.

**A sidecar never widens itself.** It says what machine it is and what
version it runs; it does not say what it may do. `capabilities` is
written at pairing by the owner and by nothing else. A compromised
sidecar can lie about its hostname and cannot lie about its authority.

**Pairing is a code, once, briefly.** Without it, anything that could
reach the server could claim to be a new pair of hands.
"""
import hashlib
import logging
import secrets
from dataclasses import dataclass
from uuid import UUID

from app.agents.schemas import Permission
from app.db import execute, fetch, fetchrow

logger = logging.getLogger("jarvis.sidecars")

# Everything a sidecar may ever be granted. A fixed vocabulary rather
# than free text: a capability nobody wrote down here cannot be granted
# by a typo, and the dashboard can list them because they are knowable.
CAPABILITIES: dict[str, str] = {
    "screenshot": "See what is on that screen",
    "browser": "Open a page on that device",
    "files.read": "Read files on that machine",
    "files.write": "Write files on that machine",
    "terminal": "Run commands on that machine",
    "clipboard": "Read and set that device's clipboard",
    "notify": "Show a notification on that device",
    "speak": "Say something aloud on that device",
}

# The two kinds of sidecar, and what each can actually perform.
#
# `native` is the Python program: a Mac, a PC, a Linux box, a Pi. It can
# do everything on the list.
#
# `browser` is the dashboard itself, open in a tab. That is the only
# sidecar an iPad or a phone can be: iOS does not allow arbitrary
# background processes, and no amount of wanting it to changes that. A
# web page can say something aloud, show a notification and put a link in
# front of the owner. It cannot run a command, read a file, or take a
# screenshot without putting a picker in his face.
#
# Enforced at PAIRING as well as at run time. Granting an iPad 'terminal'
# and letting every job for it fail for ever is the worst shape a
# limitation can take -- invisible until it matters.
KINDS: dict[str, str] = {
    "native": "A program you run on a Mac, PC, Linux box or Pi",
    "browser": "This dashboard, open in a tab on a phone, iPad or laptop",
}

KIND_CAN_DO: dict[str, frozenset[str]] = {
    "native": frozenset({"screenshot", "browser", "files.read", "files.write",
                         "terminal", "clipboard", "notify", "speak"}),
    # Deliberately short, and every one of these was checked against what
    # a page can do without a user gesture -- because a sidecar acts when
    # JARVIS asks, and nobody is there to tap.
    "browser": frozenset({"notify", "speak", "browser", "clipboard"}),
}

# Which JARVIS permission an agent must hold before it may use each
# capability. The sidecar's grant says the MACHINE allows it; this says
# the AGENT is allowed to ask. Both, always.
NEEDS: dict[str, Permission] = {
    "screenshot": Permission.READ_FILES,
    "speak": Permission.EXTERNAL_MESSAGE,
    "browser": Permission.NETWORK,
    "files.read": Permission.READ_FILES,
    "files.write": Permission.WRITE_FILES,
    "terminal": Permission.RUN_COMMAND,
    "clipboard": Permission.READ_FILES,
    "notify": Permission.EXTERNAL_MESSAGE,
}

# Capabilities that never run unattended, whatever was granted.
#
# The grant says the owner is willing for this machine to be able to do
# it at all. That is not the same as being willing for it to happen now,
# unwatched, because an agent decided it would help. These are the ones
# where being wrong is expensive and not undoable from here.
ALWAYS_ASK = frozenset({"terminal", "files.write"})

# How long a job waits for a machine that is asleep. Past this it is
# expired rather than run: the conversation it belonged to is over, and
# a screenshot taken four hours late answers a question nobody is still
# asking.
JOB_TTL_MINUTES = 10


class SidecarError(Exception):
    """Refused, and this says why in words the owner can act on."""


@dataclass(frozen=True)
class Sidecar:
    id: UUID
    name: str
    machine: str
    capabilities: frozenset[str]
    status: str
    last_seen_at: object | None
    kind: str = "native"

    @property
    def can_do(self) -> frozenset[str]:
        """What this kind of thing is physically able to perform."""
        return KIND_CAN_DO.get(self.kind, frozenset())

    @property
    def live(self) -> bool:
        """Seen recently enough to be worth queueing work for."""
        from datetime import datetime, timedelta, timezone

        if self.status != "active" or self.last_seen_at is None:
            return False
        return self.last_seen_at > datetime.now(timezone.utc) - timedelta(minutes=2)

    def as_detail(self) -> dict:
        return {
            "id": str(self.id), "name": self.name, "machine": self.machine,
            "kind": self.kind, "kind_means": KINDS.get(self.kind, ""),
            "capabilities": sorted(self.capabilities),
            "can": [CAPABILITIES[c] for c in sorted(self.capabilities)
                    if c in CAPABILITIES],
            "status": self.status, "live": self.live,
            "last_seen_at": self.last_seen_at,
        }


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _clean(capabilities, kind: str = "native") -> frozenset[str]:
    """Only capabilities that exist AND that this kind can perform."""
    if kind not in KINDS:
        raise SidecarError(
            f"A sidecar is {' or '.join(sorted(KINDS))}, not {kind!r}.")

    asked = {str(c).strip().lower() for c in (capabilities or [])}
    unknown = asked - set(CAPABILITIES)
    if unknown:
        raise SidecarError(
            f"There is no such capability as {', '.join(sorted(unknown))}. "
            f"The ones that exist: {', '.join(sorted(CAPABILITIES))}."
        )

    # Refused here rather than at the first job. An iPad granted
    # 'terminal' would take the grant happily and then fail every job
    # queued for it, for ever, for a reason knowable right now.
    impossible = asked - KIND_CAN_DO[kind]
    if impossible:
        raise SidecarError(
            f"{KINDS[kind]} cannot do "
            f"{', '.join(sorted(impossible))}. On that kind of sidecar "
            f"JARVIS can: {', '.join(sorted(KIND_CAN_DO[kind]))}."
        )
    return frozenset(asked)


# --- pairing ---------------------------------------------------------------

async def offer_pairing(name: str, capabilities, *, by: str,
                        kind: str = "native") -> dict:
    """A code the owner types into the sidecar on the other machine.

    Good once and for fifteen minutes. The capabilities are decided HERE,
    by him, before the machine has said anything at all -- so what a
    sidecar may do is never a function of what it claims to be.
    """
    name = (name or "").strip()
    if not name:
        raise SidecarError("Give the machine a name you will recognise.")
    granted = _clean(capabilities, kind)
    if not granted:
        raise SidecarError(
            "A sidecar with no capabilities can do nothing. Choose at "
            "least one of: " + ", ".join(sorted(KIND_CAN_DO[kind])))

    existing = await fetchrow(
        "SELECT id FROM sidecars WHERE name = $1 AND status <> 'revoked'", name)
    if existing:
        raise SidecarError(
            f"There is already a sidecar called {name!r}. Revoke it first, "
            f"or pick another name.")

    # Human-typable: this gets read off one screen and typed into another.
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    await execute(
        "INSERT INTO sidecar_pairings (code, name, capabilities, kind) "
        "VALUES ($1,$2,$3,$4)",
        code, name, sorted(granted), kind,
    )
    logger.info("Pairing offered for %r by %s.", name, by)
    return {"code": code, "name": name, "capabilities": sorted(granted),
            "expires_in_minutes": 15}


async def redeem_pairing(code: str, reported: dict | None = None) -> dict:
    """The sidecar's one call that is not authenticated. Returns its token.

    The token is returned once and stored only as a hash, so it cannot be
    read back out of this server by anybody -- including the owner. If he
    loses it he pairs again, which is cheap; if it could be read back,
    every future compromise of the dashboard would hand over every
    machine he owns.
    """
    row = await fetchrow(
        "SELECT * FROM sidecar_pairings WHERE code = $1 AND used_at IS NULL "
        "AND expires_at > now()",
        (code or "").strip().upper(),
    )
    if row is None:
        raise SidecarError(
            "That pairing code is wrong, already used, or older than "
            "fifteen minutes. Generate a new one.")

    token = secrets.token_urlsafe(32)
    sidecar = await fetchrow(
        "INSERT INTO sidecars (name, machine, capabilities, token_hash, "
        "reported, kind) VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
        row["name"], str((reported or {}).get("machine", ""))[:200],
        list(row["capabilities"]), _hash(token), reported or {}, row["kind"],
    )
    await execute("UPDATE sidecar_pairings SET used_at = now() WHERE code = $1",
                  row["code"])
    logger.info("Sidecar %r paired.", row["name"])
    return {"token": token, "sidecar": _to_sidecar(sidecar).as_detail()}


async def pair_this_browser(name: str, capabilities, *, by: str,
                            reported: dict | None = None) -> dict:
    """Pair the tab the owner is already looking at. One tap, no code.

    A pairing code exists so that a machine JARVIS has never met can
    prove the owner authorised it -- he reads it off one screen and types
    it into another. A browser has nothing to type it into, and it does
    not need to: it is already signed in as him. Asking him to copy a
    code from a page into that same page would be ceremony, and ceremony
    that achieves nothing is how people learn to click past security.

    This is the ONLY path that skips the code, and it is reachable only
    through his own authenticated session.
    """
    name = (name or "").strip() or "This device"
    granted = _clean(capabilities, "browser")
    if not granted:
        raise SidecarError(
            "A sidecar with no capabilities can do nothing. On a browser "
            "JARVIS can: " + ", ".join(sorted(KIND_CAN_DO["browser"])))

    # Re-pairing the same device replaces it rather than accumulating a
    # row per visit: he will open this dashboard on the same iPad many
    # times, and a list of forty "iPad" entries is a list nobody reads.
    await execute(
        "UPDATE sidecars SET status = 'revoked' "
        "WHERE name = $1 AND kind = 'browser' AND status <> 'revoked'", name)

    token = secrets.token_urlsafe(32)
    row = await fetchrow(
        "INSERT INTO sidecars (name, machine, capabilities, token_hash, "
        "reported, kind) VALUES ($1,$2,$3,$4,$5,'browser') RETURNING *",
        name, str((reported or {}).get("machine", ""))[:200],
        sorted(granted), _hash(token), reported or {},
    )
    logger.info("Browser sidecar %r paired by %s.", name, by)
    return {"token": token, "sidecar": _to_sidecar(row).as_detail()}


def _to_sidecar(row) -> Sidecar:
    return Sidecar(
        id=row["id"], name=row["name"], machine=row["machine"],
        capabilities=frozenset(row["capabilities"] or []),
        status=row["status"], last_seen_at=row["last_seen_at"],
        kind=row["kind"] if "kind" in row else "native",
    )


async def authenticate(token: str) -> Sidecar | None:
    """Which sidecar this is, or None. Never says which part was wrong."""
    if not token:
        return None
    row = await fetchrow(
        "SELECT * FROM sidecars WHERE token_hash = $1 AND status <> 'revoked'",
        _hash(token))
    return _to_sidecar(row) if row else None


async def seen(sidecar_id: UUID, reported: dict | None = None) -> None:
    """It checked in. Descriptive only -- nothing here grants anything."""
    await execute(
        "UPDATE sidecars SET last_seen_at = now(), "
        "reported = COALESCE($2, reported), "
        "machine = COALESCE(NULLIF($3, ''), machine) WHERE id = $1",
        sidecar_id, reported, str((reported or {}).get("machine", ""))[:200],
    )


# --- who may do what -------------------------------------------------------

def may_run(sidecar: Sidecar, capability: str,
            agent_holds: frozenset[Permission]) -> tuple[bool, str]:
    """The intersection, computed once, here.

    Three things have to agree: the owner granted this machine that
    capability, the agent asking holds the permission behind it, and the
    capability exists at all. Returns (allowed, why not) rather than
    raising, because the caller usually wants to say why in an answer
    rather than fail a task.
    """
    if capability not in CAPABILITIES:
        return False, f"There is no such capability as {capability!r}."
    if sidecar.status != "active":
        return False, f"{sidecar.name} is {sidecar.status}."
    # The backstop for the pairing check. A row that predates the kinds,
    # or one edited by hand, must not be able to run something its kind
    # cannot perform.
    if capability not in sidecar.can_do:
        return False, (
            f"{KINDS.get(sidecar.kind, sidecar.kind)} cannot do "
            f"'{capability}' at all.")
    if capability not in sidecar.capabilities:
        return False, (
            f"{sidecar.name} was not given '{capability}'. It can: "
            f"{', '.join(sorted(sidecar.capabilities)) or 'nothing'}.")

    needed = NEEDS[capability]
    if needed not in agent_holds:
        return False, (
            f"Using '{capability}' needs the '{needed.value}' permission, "
            f"which this agent does not hold.")
    return True, ""


def needs_approval(capability: str) -> bool:
    """Is this one the owner is asked about every time, grant or no grant."""
    return capability in ALWAYS_ASK


# --- work ------------------------------------------------------------------

async def queue(sidecar: Sidecar, capability: str, action: str,
                arguments: dict | None = None, *, task_id=None,
                agent_holds: frozenset[Permission],
                approved: bool = False) -> dict:
    """Put one job in front of one machine.

    Refuses rather than queueing something that could never run: a job
    that sits in a queue until it expires, for a reason that was knowable
    when it was written, is a failure nobody sees.
    """
    allowed, why = may_run(sidecar, capability, agent_holds)
    if not allowed:
        raise SidecarError(why)

    waiting = needs_approval(capability) and not approved
    row = await fetchrow(
        "INSERT INTO sidecar_jobs (sidecar_id, task_id, capability, action, "
        "arguments, needs_approval, expires_at) "
        "VALUES ($1,$2,$3,$4,$5,$6, now() + ($7 || ' minutes')::interval) "
        "RETURNING *",
        sidecar.id, task_id, capability, action, arguments or {},
        waiting, str(JOB_TTL_MINUTES),
    )
    await _audit(sidecar, capability, action, "queued",
                 approved="owner" if approved else None)
    return dict(row)


async def next_jobs(sidecar: Sidecar, limit: int = 5) -> list[dict]:
    """What this machine should do, now that it has asked.

    Claimed in one statement, so two copies of the same sidecar running
    by accident do not both take the same job -- somebody will leave this
    running on a Mac and start it again over SSH without noticing.

    What actually prevents the duplicate is Postgres re-checking the
    UPDATE's condition after it unblocks, under READ COMMITTED: the
    second claimer finds the row already 'sent' and takes nothing.
    `SKIP LOCKED` is here so that second claimer does not sit BLOCKED
    behind the first while it works, which on a slow home server is the
    difference between two pollers and one. Tested by claiming
    concurrently; removing SKIP LOCKED still hands each job out once,
    which is why that is written down here rather than implied.

    The expiry is checked in the claim as well as by the sweep. A job
    that expired thirty seconds ago must not be handed out because the
    sweep has not run yet.
    """
    rows = await fetch(
        """
        UPDATE sidecar_jobs SET status = 'sent', sent_at = now()
        WHERE id IN (
            SELECT id FROM sidecar_jobs
             WHERE sidecar_id = $1 AND status = 'queued'
               AND needs_approval = FALSE AND expires_at > now()
             ORDER BY created_at LIMIT $2
             FOR UPDATE SKIP LOCKED
        )
        RETURNING id, capability, action, arguments, expires_at
        """,
        sidecar.id, limit,
    )
    return [dict(r) for r in rows]


async def finish(sidecar: Sidecar, job_id: UUID, *, ok: bool,
                 result: dict | None = None, error: str = "") -> bool:
    """What came back. Only from the sidecar the job was given to."""
    outcome = "done" if ok else "failed"
    done = await execute(
        "UPDATE sidecar_jobs SET status = $3, result = $4, error = $5, "
        "finished_at = now() WHERE id = $1 AND sidecar_id = $2 "
        "AND status = 'sent'",
        job_id, sidecar.id, outcome, result, (error or "")[:2000],
    )
    if not done.endswith("1"):
        return False
    await _audit(sidecar, "", "", outcome)
    return True


async def job(job_id: UUID) -> dict | None:
    row = await fetchrow("SELECT * FROM sidecar_jobs WHERE id = $1", job_id)
    return dict(row) if row else None


async def expire_old() -> int:
    """A machine that was asleep has jobs waiting, not jobs pending for ever."""
    done = await execute(
        "UPDATE sidecar_jobs SET status = 'expired', finished_at = now() "
        "WHERE status IN ('queued','sent') AND expires_at < now()")
    return int(done.rsplit(" ", 1)[-1] or 0)


# --- the owner's controls --------------------------------------------------

async def listing() -> list[dict]:
    rows = await fetch(
        "SELECT * FROM sidecars WHERE status <> 'revoked' ORDER BY name")
    return [_to_sidecar(r).as_detail() for r in rows]


async def set_status(name: str, status: str, *, by: str) -> bool:
    """Pause, resume or revoke a machine.

    Revoking is final: the token stops working and pairing again is a new
    row. That is deliberate -- "this laptop was stolen" should not be
    undoable by whoever has the laptop.
    """
    if status not in ("active", "paused", "revoked"):
        raise SidecarError("A sidecar is active, paused or revoked.")
    done = await execute(
        "UPDATE sidecars SET status = $2 WHERE name = $1 AND status <> 'revoked'",
        name, status)
    if done.endswith("1"):
        logger.info("Sidecar %r set to %s by %s.", name, status, by)
        if status in ("paused", "revoked"):
            # Nothing queued should run on a machine the owner has just
            # stood down, including work already handed out.
            await execute(
                "UPDATE sidecar_jobs SET status = 'cancelled', finished_at = now() "
                "WHERE status IN ('queued','sent') AND sidecar_id = "
                "(SELECT id FROM sidecars WHERE name = $1)", name)
    return done.endswith("1")


async def recent(limit: int = 25) -> list[dict]:
    rows = await fetch(
        "SELECT j.*, s.name AS sidecar FROM sidecar_jobs j "
        "JOIN sidecars s ON s.id = j.sidecar_id "
        "ORDER BY j.created_at DESC LIMIT $1", limit)
    return [dict(r) for r in rows]


async def waiting_for_owner() -> list[dict]:
    rows = await fetch(
        "SELECT j.*, s.name AS sidecar FROM sidecar_jobs j "
        "JOIN sidecars s ON s.id = j.sidecar_id "
        "WHERE j.status = 'queued' AND j.needs_approval "
        "AND j.expires_at > now() ORDER BY j.created_at")
    return [dict(r) for r in rows]


async def approve(job_id: UUID, *, by: str) -> bool:
    """Let one waiting job through. One job, not a category."""
    done = await execute(
        "UPDATE sidecar_jobs SET needs_approval = FALSE "
        "WHERE id = $1 AND status = 'queued' AND needs_approval", job_id)
    if done.endswith("1"):
        logger.info("Sidecar job %s approved by %s.", job_id, by)
    return done.endswith("1")


async def _audit(sidecar: Sidecar, capability: str, action: str,
                 outcome: str, approved: str | None = None) -> None:
    """Every job, on the record. Never fails the thing it is recording."""
    from app.audit import log_audit

    try:
        await log_audit(
            actor=f"sidecar:{sidecar.name}",
            action=f"sidecar {outcome}: {capability}.{action}".strip(". "),
            # Reaching another of the owner's machines is never low risk.
            category="high_risk" if capability in ALWAYS_ASK else "medium_risk",
            outcome=outcome, approved_by=approved,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not record a sidecar job: %s", exc)


async def state() -> dict:
    """For the dashboard, and for "what can you actually reach?"."""
    machines = await listing()
    return {
        "capabilities": [{"name": k, "means": v} for k, v in CAPABILITIES.items()],
        "kinds": [{"kind": k, "means": v, "can_do": sorted(KIND_CAN_DO[k])}
                  for k, v in KINDS.items()],
        "always_ask": sorted(ALWAYS_ASK),
        "sidecars": machines,
        "waiting": len(await waiting_for_owner()),
        "said": (
            "No other machines are connected. JARVIS can only reach its own "
            "server." if not machines else
            f"{sum(1 for m in machines if m['live'])} of {len(machines)} "
            f"connected right now."
        ),
    }
