"""The Governor: what JARVIS may change about itself, and on whose say-so.

The brief says the Governor already exists and must be extended rather
than duplicated. That is very nearly true and worth being precise about,
because the imprecision is where a second, parallel authority gets built
by accident.

There is no `Governor` class in this codebase and never was. What exists
is its *responsibilities*, spread across five modules that each do their
job well:

    constitution.py  -- what may never be written
    permissions.py   -- what an agent may do at all
    approvals.py     -- what the owner has already answered
    budget.py        -- what may be spent
    system_control.py-- the stop that overrides everything

This module does not replace any of them and does not re-implement a
single one of their decisions. It is the place where a *change to JARVIS
itself* is weighed, and it answers that question by asking them. If you
find a rule here that also exists in one of those files, that is a bug:
delete it here and call theirs.

What it adds that none of them had: a **risk level** for a proposed
change, and an **autonomy ceiling** the owner sets. Those two numbers are
the whole boundary. Level 4 is refused outright and no setting can permit
it. Everything else is compared against the ceiling, which starts at
"ask me about everything" and only the owner can raise.

**The direction this is wrong in, on purpose.** Every uncertainty
resolves upward: a path nobody recognises is Level 3, not Level 1; a
change touching two levels takes the higher; a missing test result is
treated as a failing one. Being wrong the safe way means JARVIS asks
about something it could have handled. Being wrong the other way means it
does something it should have asked about, and the owner finds out
afterwards.
"""
import fnmatch
import logging
from dataclasses import dataclass, field

from app import constitution

logger = logging.getLogger("jarvis.governor")

# The brief, section 10. These are the words the owner used, kept as the
# words the owner used, so the dashboard and the refusals read the same.
LEVELS: dict[int, str] = {
    0: "read only",
    1: "low risk",
    2: "normal development",
    3: "high risk",
    4: "protected",
}

DESCRIBED: dict[int, str] = {
    0: "Inspection and analysis. Nothing is written.",
    1: "Styling, layout, copy, documentation. Nothing that can change "
       "what JARVIS decides.",
    2: "Ordinary development: agents, tools, workflows, integrations.",
    3: "Memory, the orchestrator, model routing, the database schema, "
       "deployment. A mistake here is felt everywhere.",
    4: "The Constitution, the Governor, permissions, credentials, "
       "authority. Never changed this way, at any autonomy setting.",
}

# Level 3, checked before level 1 and 2 because the specific must beat
# the general: `core/app/memory*.py` is high risk even though `core/app`
# is ordinary, and `static/` copy is low risk but `static/` is not
# automatically so.
#
# Straight from the brief's own list: memory, authentication, database
# schema, orchestrator, model routing, computer control, browser
# execution, network architecture, deployment infrastructure.
HIGH_RISK: tuple[tuple[str, str], ...] = (
    ("core/app/memory*.py", "memory"),
    ("core/app/memory/*", "memory"),
    ("core/app/db.py", "the database connection"),
    ("core/app/migrate.py", "how the schema is applied"),
    ("db/*", "the database schema"),
    ("core/app/agents/orchestrator.py", "the orchestrator"),
    ("core/app/agents/runtime.py", "the one execution path"),
    ("core/app/agents/planner.py", "how work is planned"),
    ("core/app/agents/registry.py", "what agents exist"),
    ("core/app/agents/model_router.py", "model routing"),
    ("core/app/agents/cost.py", "how spending is counted"),
    ("core/app/llm/*", "model routing"),
    ("core/app/governor.py", "the risk classification itself"),
    ("core/app/dev/*", "self-development"),
    ("core/app/scheduler.py", "what runs without being asked"),
    ("core/app/main.py", "startup"),
    ("core/app/config.py", "configuration"),
    ("core/app/attachments.py", "what JARVIS is allowed to read"),
    ("core/requirements.txt", "dependencies"),
    ("Dockerfile", "deployment infrastructure"),
    ("*.dockerignore", "what reaches the image"),
    (".github/*", "what runs on every push"),
)
# Deliberately absent from that list: infra/*, render.yaml,
# docker-compose.yml, core/app/governor.py and core/app/dev/auditor.py.
# The Constitution already makes all five Level 4, which is stricter.
# Listing them here as Level 3 would be unreachable code that reads as
# though it were doing something -- and would be actively misleading to
# anyone checking whether deployment config can be changed automatically.
# A test asserts they stay at 4.

# Level 1. Appearance and words: things a person can look at and judge.
LOW_RISK: tuple[tuple[str, str], ...] = (
    ("*.md", "documentation"),
    ("docs/*", "documentation"),
    ("core/static/*.css", "styling"),
    ("*.css", "styling"),
)

# Level 2 is everything else that is code. Named for the message rather
# than for matching.
NORMAL = "ordinary application code"


@dataclass(frozen=True)
class Verdict:
    """What a set of paths adds up to."""
    level: int
    reasons: tuple[str, ...] = ()
    per_path: tuple[tuple[str, int, str], ...] = ()

    @property
    def name(self) -> str:
        return LEVELS[self.level]

    def as_detail(self) -> dict:
        return {
            "level": self.level,
            "name": self.name,
            "means": DESCRIBED[self.level],
            "reasons": list(self.reasons),
            "files": [{"path": p, "level": lv, "why": w}
                      for p, lv, w in self.per_path],
        }


@dataclass(frozen=True)
class Decision:
    """Refuse, ask, or let it stand."""
    outcome: str                      # "refused" | "ask" | "autonomous"
    why: str
    level: int = 2
    blocking: tuple[str, ...] = ()
    ceiling: int = 0
    detail: dict = field(default_factory=dict)

    def as_detail(self) -> dict:
        return {"outcome": self.outcome, "why": self.why, "level": self.level,
                "ceiling": self.ceiling, "blocking": list(self.blocking),
                **self.detail}


def level_of(path: str) -> tuple[int, str]:
    """One path's risk level, and why it has it.

    Order is the design. Protected first, because nothing overrides it.
    High risk next, because specific patterns must beat the general rule
    that Python under core/app is ordinary. Low risk last, so that a
    stylesheet is low risk but `infra/style.css` -- if such a thing ever
    existed -- is not.
    """
    protected = constitution.is_protected(path)
    if protected:
        return 4, protected

    normalised = constitution._normalise(path)

    for pattern, why in HIGH_RISK:
        if fnmatch.fnmatch(normalised, pattern):
            return 3, why
        # A directory pattern covers what is under it.
        if pattern.endswith("/*") and normalised.startswith(pattern[:-1]):
            return 3, why

    for pattern, why in LOW_RISK:
        if fnmatch.fnmatch(normalised, pattern):
            return 1, why
        if pattern.endswith("/*") and normalised.startswith(pattern[:-1]):
            return 1, why

    return 2, NORMAL


def classify(paths) -> Verdict:
    """The risk of a whole change: the riskiest thing in it.

    Not an average and not a majority. A change that edits forty
    stylesheets and one file that decides who is the owner is a change
    that decides who is the owner.

    An empty change is Level 0 -- nothing is written, so nothing is at
    risk -- which is also what a plan that turned out not to be a change
    should come back as.
    """
    listed = [p for p in (paths or []) if p]
    if not listed:
        return Verdict(level=0, reasons=("Nothing is written.",))

    per_path, reasons = [], []
    for path in listed:
        level, why = level_of(path)
        per_path.append((path, level, why))

    top = max(level for _, level, _ in per_path)
    for path, level, why in per_path:
        if level == top:
            reasons.append(f"{path} -- {why}")

    return Verdict(level=top, reasons=tuple(dict.fromkeys(reasons)),
                   per_path=tuple(per_path))


async def ceiling() -> int:
    """The highest level the owner has said JARVIS may approve for itself.

    Starts at 0, which means: JARVIS may decide nothing by itself. Raising
    it is a deliberate act by the owner, recorded with who did it and
    when, and it can never reach 4 -- the database refuses that, and so
    does `decide` below, because a single check is a single point of
    failure for the one rule that matters most.
    """
    from app.db import fetchrow

    row = await fetchrow(
        "SELECT max_autonomous_level FROM governor_policy WHERE id = TRUE"
    )
    if row is None:
        return 0
    return max(0, min(3, int(row["max_autonomous_level"])))


async def set_ceiling(level: int, decided_by: str) -> int:
    """Raise or lower the ceiling. Only the owner reaches this.

    Clamped to 0-3 rather than rejected above 3, so that an attempt to
    set 4 lands on 3 instead of failing open somewhere upstream.
    """
    from app.db import fetchrow

    wanted = max(0, min(3, int(level)))
    row = await fetchrow(
        """
        INSERT INTO governor_policy (id, max_autonomous_level, updated_by)
        VALUES (TRUE, $1, $2)
        ON CONFLICT (id) DO UPDATE
            SET max_autonomous_level = EXCLUDED.max_autonomous_level,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
        RETURNING max_autonomous_level
        """,
        wanted, decided_by,
    )
    logger.warning("Self-development autonomy ceiling set to %s by %s.",
                   wanted, decided_by)
    return int(row["max_autonomous_level"])


async def review(
    *,
    paths,
    tests_passed: bool | None,
    audit: dict | None = None,
    spend_inr=None,
) -> Decision:
    """The one decision point. Everything about a change ends up here.

    Four questions, in an order chosen so the answer cannot be reached by
    getting three of them right:

      1. Is the emergency stop on?          -- nothing proceeds.
      2. Is anything in it Level 4?         -- refused, at any setting.
      3. Did anything fail?                 -- tests, the auditor, budget.
      4. Is its level within the ceiling?   -- if not, the owner is asked.

    "Ask" is the default and the fallback. Every path that is not an
    explicit yes ends there, including the ones that arrive with
    something missing.
    """
    from app import system_control

    verdict = classify(paths)
    detail = {"risk": verdict.as_detail()}
    allowed = await ceiling()

    stopped = await system_control.is_stopped()
    if stopped:
        return Decision(
            outcome="refused", level=verdict.level, ceiling=allowed,
            why="Everything is stopped. Nothing is approved while the "
                "emergency stop is on.",
            blocking=("the emergency stop is on",), detail=detail,
        )

    if verdict.level >= 4:
        return Decision(
            outcome="refused", level=4, ceiling=allowed,
            why="This change reaches the protected core, which ordinary "
                "self-development never changes -- at any autonomy "
                "setting. " + " ".join(verdict.reasons),
            blocking=verdict.reasons, detail=detail,
        )

    blocking: list[str] = []
    if tests_passed is False:
        blocking.append("the tests fail")
    elif tests_passed is None:
        # Not the same as failing, and treated the same. An unknown test
        # result is the state a change is in when nobody looked.
        blocking.append("the tests did not run, so nothing was verified")

    findings = list((audit or {}).get("blocking") or [])
    blocking.extend(findings)

    if spend_inr is not None:
        from app import budget

        try:
            left = await budget.remaining()
        except Exception:  # noqa: BLE001 - never block on the meter itself
            left = None
        if left is not None and left <= 0:
            blocking.append("the monthly budget is spent")

    if blocking:
        return Decision(
            outcome="ask", level=verdict.level, ceiling=allowed,
            why="This needs you to look at it: " + "; ".join(blocking) + ".",
            blocking=tuple(blocking), detail=detail,
        )

    if verdict.level > allowed:
        return Decision(
            outcome="ask", level=verdict.level, ceiling=allowed,
            why=f"This is {verdict.name} (level {verdict.level}) and you "
                f"have allowed JARVIS to approve up to level {allowed} "
                f"({LEVELS[allowed]}) by itself.",
            detail=detail,
        )

    return Decision(
        outcome="autonomous", level=verdict.level, ceiling=allowed,
        why=f"{verdict.name.capitalize()} (level {verdict.level}), within "
            f"the level {allowed} you allowed, tests pass and the auditor "
            f"found nothing blocking.",
        detail=detail,
    )


async def state() -> dict:
    """What the Governor is currently allowing, for the dashboard."""
    allowed = await ceiling()
    return {
        "ceiling": allowed,
        "ceiling_name": LEVELS[allowed],
        "means": (
            "JARVIS asks you about every change it writes."
            if allowed == 0 else
            f"JARVIS may approve its own changes up to level {allowed} "
            f"({LEVELS[allowed]}). Anything above that still comes to you."
        ),
        "levels": [{"level": n, "name": LEVELS[n], "means": DESCRIBED[n]}
                   for n in sorted(LEVELS)],
        "never_autonomous": LEVELS[4],
    }
