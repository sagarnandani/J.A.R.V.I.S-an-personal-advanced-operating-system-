"""Trying a new version of an agent against the one already doing the job.

Section 20: candidate -> benchmark -> promotion.

The registry has kept version history since the agent foundation, and it
will stand one version down and another up on request. What never
existed is the middle word. Without a benchmark, "promotion" means
somebody looked at the new one and thought it seemed better -- which is
the judgement this whole system exists to replace with a measurement.

**How a trial runs.** A share of the capability's tasks go to the
candidate; the rest go to the version already live. Both are measured by
the machinery that was already measuring everything, because
`agent_metrics` has recorded `agent_id` from the beginning -- so which
VERSION produced a result was answerable all along, in the same way
which model produced it was not until migration 011.

**Which arm a task lands on is decided by the task's own id**, not by a
coin. Two reasons, and the second is the one that matters. It makes a
trial reproducible. And a retry keeps the arm it started on: a random
split would send a candidate's failure to the baseline on the retry, the
baseline would succeed, and the candidate's failure would be hidden by
the very mechanism meant to measure it.

**A trial may not widen what an agent may do.** A candidate holding a
permission the live version does not is not a candidate, it is a
privilege escalation with a version number, and it would arrive through
a door marked "we are just trying this". Refused at the start.

**Three honest answers, and two of them are not "promote".** Both arms
need enough runs. The candidate must be better by a *margin*, because a
difference smaller than the noise is not a difference -- promoting on it
is promoting randomness and calling it improvement. Short of that the
verdict is "keep watching" or "no difference worth acting on", which are
real answers.

**Nothing promotes itself.** `verdict` recommends; `promote` acts, and
something outside has to call it -- the owner's tap, or the Governor
within its ceiling. An agent that could promote its own successor is an
agent that can change what runs on the owner's behalf without being
asked, which is the thing the Constitution exists to prevent.
"""
import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal

from app.agents import registry
from app.agents.schemas import Lifecycle
from app.db import execute, fetch, fetchrow

logger = logging.getLogger("jarvis.trials")

# Runs needed on EACH arm before the comparison means anything. Higher
# than the model-performance floor: there, one model's record is being
# read on its own; here two small numbers are being subtracted, and the
# noise in a difference is bigger than the noise in either side.
ENOUGH_EACH = 12

# How much better the candidate has to be. Below this the two are the
# same as far as this data can tell, and promoting on it is promoting
# noise.
MARGIN = 0.10

# The same margin the other way. A candidate this much worse is not
# "still being watched" -- it is doing harm on a share of real work.
WORSE = 0.10

DEFAULT_SHARE = Decimal("0.20")

PROMOTE = "promote"
REJECT = "reject"
WATCH = "keep_watching"
CANNOT_SAY = "not_enough_yet"


class CannotTrial(Exception):
    """The trial will not start, and this says why in plain words."""


@dataclass(frozen=True)
class Arm:
    version: int
    agent_id: str | None
    runs: int
    successes: int
    avg_confidence: float | None
    avg_cost_inr: float | None
    avg_latency_ms: float | None

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs if self.runs else 0.0

    @property
    def enough(self) -> bool:
        return self.runs >= ENOUGH_EACH

    def as_detail(self) -> dict:
        return {"version": self.version, "runs": self.runs,
                "success_rate": round(self.success_rate, 3),
                "avg_confidence": (round(self.avg_confidence, 2)
                                   if self.avg_confidence else None),
                "avg_cost_inr": (round(self.avg_cost_inr, 4)
                                 if self.avg_cost_inr else None),
                "avg_latency_ms": (round(self.avg_latency_ms)
                                   if self.avg_latency_ms else None),
                "enough_to_judge": self.enough}


@dataclass(frozen=True)
class Verdict:
    call: str
    candidate: Arm
    baseline: Arm
    said: str

    @property
    def difference(self) -> float:
        # Rounded the same way `judge` rounds it, or the number shown to
        # the owner would disagree with the decision made from it.
        return round(self.candidate.success_rate - self.baseline.success_rate, 6)

    def as_detail(self) -> dict:
        return {"call": self.call, "said": self.said,
                "difference": round(self.difference, 3),
                "margin": MARGIN, "runs_needed_each": ENOUGH_EACH,
                "candidate": self.candidate.as_detail(),
                "baseline": self.baseline.as_detail()}


# --- starting one ----------------------------------------------------------

async def start(capability: str, candidate_version: int, *, by: str,
                share: Decimal = DEFAULT_SHARE) -> dict:
    """Begin a trial. Refuses rather than starting a meaningless one."""
    live = await registry.resolve(capability)
    if live is None:
        raise CannotTrial(
            f"Nothing is live for '{capability}', so there is nothing to "
            f"compare a new version against.")

    candidate = await registry.get(capability, candidate_version)
    if candidate is None:
        raise CannotTrial(
            f"There is no version {candidate_version} of '{capability}'.")
    if candidate.version == live.version:
        raise CannotTrial(
            f"Version {candidate_version} of '{capability}' is the one "
            f"already doing the job.")

    # The security one. A candidate that may do more than the version it
    # would replace is not a candidate.
    widened = candidate.permissions - live.permissions
    if widened:
        raise CannotTrial(
            f"Version {candidate_version} of '{capability}' would hold "
            f"{', '.join(sorted(p.value for p in widened))}, which the live "
            f"version does not. A trial may compare how well something "
            f"works; it may not be how an agent quietly gains a permission."
        )

    if await running(capability):
        raise CannotTrial(
            f"'{capability}' already has a trial running. Two at once makes "
            f"'which change caused this' unanswerable, which is the only "
            f"question a trial answers.")

    share = Decimal(str(share))
    if not (Decimal("0") < share <= Decimal("0.5")):
        raise CannotTrial(
            "A trial share must be above 0 and at most 0.5. Sending most of "
            "the work to an unproven version is a deployment, not a trial.")

    row = await fetchrow(
        """
        INSERT INTO agent_trials (capability, candidate_version,
                                  baseline_version, share, started_by)
        VALUES ($1,$2,$3,$4,$5) RETURNING *
        """,
        capability, candidate_version, live.version, share, by,
    )
    # Routable only when asked for by name is exactly what a candidate
    # is, and `arm` below is the thing doing the asking.
    await registry.set_status(capability, candidate_version, Lifecycle.TESTING)
    logger.info("Trial started on %s: v%s against v%s at %s share.",
                capability, candidate_version, live.version, share)
    return dict(row)


async def running(capability: str) -> dict | None:
    row = await fetchrow(
        "SELECT * FROM agent_trials WHERE capability = $1 AND status = 'running'",
        capability,
    )
    return dict(row) if row else None


async def history(capability: str | None = None, limit: int = 20) -> list[dict]:
    rows = await fetch(
        "SELECT * FROM agent_trials WHERE ($1::text IS NULL OR capability = $1) "
        "ORDER BY started_at DESC LIMIT $2",
        capability, limit,
    )
    return [dict(r) for r in rows]


# --- which arm a task lands on --------------------------------------------

def takes_candidate(trial: dict, task_id) -> bool:
    """Decided by the task's own id, so a retry keeps the arm it started on.

    A random split would send a candidate's failure to the baseline on
    the retry; the baseline would succeed; and the candidate's failure
    would be hidden by the very mechanism meant to measure it.
    """
    if not trial:
        return False
    digest = hashlib.sha256(str(task_id).encode()).digest()
    # The first four bytes as a fraction of their range. Uniform enough
    # for a split, and the same answer on every machine and every run.
    position = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return position < float(trial["share"])


async def version_for(capability: str, task_id, live):
    """The spec that should run this task. The live one unless trialled."""
    trial = await running(capability)
    if not trial or not takes_candidate(trial, task_id):
        return live, None

    candidate = await registry.get(capability, trial["candidate_version"])
    if candidate is None:  # pragma: no cover - the row went away mid-trial
        logger.warning("Trial on %s names a version that is gone.", capability)
        return live, None
    return candidate, trial


# --- reading the result ----------------------------------------------------

async def _arm(capability: str, version: int) -> Arm:
    spec = await registry.get(capability, version)
    agent_id = str(spec.id) if spec and spec.id else None
    if agent_id is None:
        return Arm(version, None, 0, 0, None, None, None)

    row = await fetchrow(
        """
        SELECT COUNT(*) FILTER (WHERE metric = 'success')            AS runs,
               COALESCE(SUM(value) FILTER (WHERE metric = 'success'), 0) AS wins,
               AVG(value) FILTER (WHERE metric = 'confidence')       AS confidence,
               AVG(value) FILTER (WHERE metric = 'cost_inr')         AS cost,
               AVG(value) FILTER (WHERE metric = 'latency_ms')       AS latency
          FROM agent_metrics
         WHERE capability = $1 AND agent_id = $2::uuid
           AND created_at > (SELECT started_at FROM agent_trials
                              WHERE capability = $1
                              ORDER BY started_at DESC LIMIT 1)
        """,
        capability, agent_id,
    )
    runs = int((row and row["runs"]) or 0)
    return Arm(
        version=version, agent_id=agent_id, runs=runs,
        successes=int((row and row["wins"]) or 0),
        avg_confidence=float(row["confidence"]) if row and row["confidence"] else None,
        avg_cost_inr=float(row["cost"]) if row and row["cost"] else None,
        avg_latency_ms=float(row["latency"]) if row and row["latency"] else None,
    )


async def verdict(capability: str) -> Verdict | None:
    """What the numbers say. Recommends; never acts."""
    trial = await running(capability)
    if trial is None:
        return None

    candidate = await _arm(capability, trial["candidate_version"])
    baseline = await _arm(capability, trial["baseline_version"])
    return judge(candidate, baseline)


def judge(candidate: Arm, baseline: Arm) -> Verdict:
    """The decision, separated from the database so it can be read.

    Pure on purpose: everything about whether one version beat another
    is here, in eight lines, where it can be argued with.
    """
    if not candidate.enough or not baseline.enough:
        short = candidate if not candidate.enough else baseline
        return Verdict(
            CANNOT_SAY, candidate, baseline,
            f"Not enough yet: v{short.version} has {short.runs} of the "
            f"{ENOUGH_EACH} runs it takes before a difference means anything.")

    # Rounded before it is compared. 60% minus 50% is 0.09999999999999998
    # in binary, so a candidate that beat the baseline by exactly the
    # margin fell a hair short of it and was held back for ever -- and
    # which comparisons that hit depends on the run counts, so it would
    # have looked like the threshold moving about on its own.
    difference = round(candidate.success_rate - baseline.success_rate, 6)
    if difference >= MARGIN:
        return Verdict(
            PROMOTE, candidate, baseline,
            f"v{candidate.version} succeeded {candidate.success_rate:.0%} of "
            f"the time against v{baseline.version}'s "
            f"{baseline.success_rate:.0%}, over {candidate.runs} and "
            f"{baseline.runs} runs. That is a real difference.")
    if difference <= -WORSE:
        return Verdict(
            REJECT, candidate, baseline,
            f"v{candidate.version} is doing worse: {candidate.success_rate:.0%} "
            f"against v{baseline.version}'s {baseline.success_rate:.0%}. It is "
            f"taking a share of real work, so it should stop.")
    return Verdict(
        WATCH, candidate, baseline,
        f"No difference worth acting on yet: {candidate.success_rate:.0%} "
        f"against {baseline.success_rate:.0%}, and anything under "
        f"{MARGIN:.0%} is inside the noise.")


# --- deciding --------------------------------------------------------------

async def promote(capability: str, *, by: str, force: bool = False) -> dict:
    """Stand the candidate up. Refuses unless the numbers say so.

    `force` is the owner overruling the measurement, which is his to do
    -- but it is recorded as that rather than as a promotion the data
    supported.
    """
    trial = await running(capability)
    if trial is None:
        raise CannotTrial(f"'{capability}' has no trial running.")

    call = await verdict(capability)
    if not force and call.call != PROMOTE:
        raise CannotTrial(
            f"The numbers do not support promoting v"
            f"{trial['candidate_version']} of '{capability}'. {call.said}")

    await registry.set_status(capability, trial["candidate_version"],
                              Lifecycle.ACTIVE)
    detail = call.as_detail() | {"forced": bool(force)}
    await _close(trial["id"], "promoted", by, detail)
    logger.info("Promoted %s v%s%s.", capability, trial["candidate_version"],
                " (forced)" if force else "")
    return detail


async def reject(capability: str, *, by: str, reason: str = "") -> dict:
    """Stand the candidate down. The baseline keeps the job.

    Disabled rather than retired: it stays in the registry to be compared
    against, which is the whole reason versions are never edited in
    place.
    """
    trial = await running(capability)
    if trial is None:
        raise CannotTrial(f"'{capability}' has no trial running.")

    call = await verdict(capability)
    await registry.set_status(capability, trial["candidate_version"],
                              Lifecycle.DISABLED)
    detail = call.as_detail() | {"reason": reason}
    await _close(trial["id"], "rejected", by, detail)
    return detail


async def abandon(capability: str, *, by: str, reason: str = "") -> dict:
    """Stop the trial without judging it. The candidate stays as it was."""
    trial = await running(capability)
    if trial is None:
        raise CannotTrial(f"'{capability}' has no trial running.")
    detail = {"reason": reason or "stopped without a decision"}
    await _close(trial["id"], "abandoned", by, detail)
    return detail


async def _close(trial_id, status: str, by: str, detail: dict) -> None:
    await execute(
        "UPDATE agent_trials SET status = $2, ended_at = now(), "
        "decided_by = $3, verdict = $4 WHERE id = $1",
        trial_id, status, by, detail,
    )


async def state(capability: str | None = None) -> dict:
    """For the dashboard, and for answering "is anything being tried?"."""
    live = await running(capability) if capability else None
    call = await verdict(capability) if live else None
    return {
        "runs_needed_each": ENOUGH_EACH,
        "margin": MARGIN,
        "running": (dict(live) | {"verdict": call.as_detail() if call else None})
                   if live else None,
        "recent": await history(capability),
        "said": (
            call.said if call else
            f"No trial running on {capability}." if capability else
            "Nothing is being trialled."
        ),
    }
