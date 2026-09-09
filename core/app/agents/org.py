"""The organisation, read out of the registry rather than drawn by hand.

There is no diagram in this file and no list of agents. Everything below
is a query: who exists, who they report to, what they are doing right
now, how it has been going and what it has cost. An agent registered
tomorrow appears tomorrow, an agent retired disappears, and a specialist
moved under a different supervisor moves in the tree -- because the tree
*is* the registry, not a picture of it.

Two nodes are not agents, and the shape says so rather than pretending
otherwise:

* **JARVIS** itself. It is the orchestrator -- it plans, routes and
  settles -- and it holds no capability, so it is not in the registry and
  never will be.
* **A supervisor nobody registered.** `media.scout` reports to
  `media.director`, which is a recipe and a set of gates rather than an
  agent that thinks. It appears as a coordinator so the reporting line is
  visible, and its detail says exactly what it is.

The alternative -- inventing registry rows for both -- would put things
in the registry that cannot run, and the registry is what the runtime
routes on. A fake row there is a fake agent everywhere.
"""
import logging

from app.agents import registry
from app.agents.schemas import (
    ALWAYS_APPROVED,
    NEVER_DELEGATED,
    AgentSpec,
    Lifecycle,
    Permission,
)
from app.db import fetch, fetchrow

logger = logging.getLogger("jarvis.agents.org")

ROOT = "jarvis"

# What each level is, in one line, for the panel.
KINDS = {
    "orchestrator": "Plans work, routes it, and decides what it amounts to.",
    "coordinator": "Owns a domain: decides what work is required and what "
                   "its results mean. Not a model call.",
    "specialist": "Does one job through one model call.",
    "governance": "Watches the rest of the system rather than doing its work.",
}

# Permissions in words the owner would use, because "external_message"
# means nothing at a glance and the whole point of showing permissions is
# that they can be read at a glance.
PERMISSION_WORDS = {
    Permission.READ_MEMORY: "Read what JARVIS remembers",
    Permission.WRITE_MEMORY: "Write into JARVIS's memory",
    Permission.READ_FILES: "Read files",
    Permission.WRITE_FILES: "Write files",
    Permission.NETWORK: "Look things up on the live web",
    Permission.EXTERNAL_MESSAGE: "Send messages out — email, chat",
    Permission.PUBLISH: "Publish where the world can see it",
    Permission.SPEND: "Spend real money",
    Permission.DELETE: "Delete things outright",
    Permission.SENSITIVE: "Touch credentials, money or health data",
    Permission.MODIFY_CONFIG: "Change how JARVIS runs",
    Permission.MODIFY_AGENTS: "Change what agents exist or may do",
}

# How many runs before a success rate means anything. Below this the
# health says so instead of turning one bad afternoon into a verdict.
ENOUGH_RUNS = 3


def _kind(spec: AgentSpec) -> str:
    """Which level of the organisation this agent sits at.

    A rule, not a list. JARVIS Scientist does not exist yet; when it is
    registered under the governance domain it will be drawn differently
    without a line of this file changing -- which is the difference
    between a living chart and a drawing of one.
    """
    if spec.domain == "governance" or spec.capability.startswith("scientist."):
        return "governance"
    return "specialist"


def _health(lifecycle: str, runs: int, successes: float | None) -> str:
    """One word for "is this agent all right".

    Deliberately refuses to answer on thin evidence. Two runs and one
    failure is not a 50% failure rate, it is two runs, and reporting it
    as a rate would have the owner retiring a capability over a bad
    afternoon.
    """
    if lifecycle in ("disabled", "retired"):
        return "off"
    if lifecycle == "degraded":
        return "watch"
    if runs < ENOUGH_RUNS or successes is None:
        return "unproven"
    if successes < 0.6:
        return "watch"
    if successes < 0.9:
        return "mixed"
    return "ok"


async def _live() -> dict[str, dict]:
    """What every capability is doing this second, counted from tasks."""
    rows = await fetch(
        """
        SELECT t.capability,
               count(*) FILTER (WHERE t.status = 'running')              AS running,
               count(*) FILTER (WHERE t.status = 'waiting_approval')     AS waiting,
               count(*) FILTER (WHERE t.status IN ('queued','blocked'))  AS queued
          FROM tasks t
          LEFT JOIN workflows w ON w.id = t.workflow_id
         WHERE t.capability IS NOT NULL
           AND t.status IN ('running','waiting_approval','queued','blocked')
           -- Work whose workflow already ended is not queued, whatever the
           -- task row says. A failed workflow leaves its dependents
           -- blocked for ever, and counting those would show an agent as
           -- busy with work that will never run.
           AND (w.id IS NULL OR w.status NOT IN ('completed','failed','cancelled'))
         GROUP BY t.capability
        """
    )
    return {r["capability"]: dict(r) for r in rows}


async def _record() -> dict[str, dict]:
    """Runs and success rate per capability, over thirty days."""
    rows = await fetch(
        """
        SELECT capability,
               count(*) FILTER (WHERE metric = 'success')  AS runs,
               avg(value) FILTER (WHERE metric = 'success') AS success_rate
          FROM agent_metrics
         WHERE created_at > now() - interval '30 days'
         GROUP BY capability
        """
    )
    return {r["capability"]: dict(r) for r in rows}


def _state(live: dict | None) -> str:
    if not live:
        return "idle"
    if live.get("running"):
        return "working"
    if live.get("waiting"):
        return "waiting"
    if live.get("queued"):
        return "queued"
    return "idle"


async def tree() -> dict:
    """The whole organisation, as one nested structure.

    Every registered version is considered, including the ones that are
    not routable: an experimental or disabled agent is part of the
    organisation and hiding it would make the chart a chart of the happy
    path.
    """
    specs = await registry.find(routable_only=False)
    live = await _live()
    record = await _record()
    known = {s.capability for s in specs}

    def node(spec: AgentSpec) -> dict:
        seen = record.get(spec.capability) or {}
        runs = int(seen.get("runs") or 0)
        rate = seen.get("success_rate")
        rate = float(rate) if rate is not None else None
        return {
            "id": spec.capability,
            "kind": _kind(spec),
            "name": spec.name,
            "role": spec.description,
            "domain": spec.domain,
            "supervisor": spec.supervisor,
            "version": spec.version,
            "lifecycle": spec.status.value,
            "state": _state(live.get(spec.capability)),
            "health": _health(spec.status.value, runs, rate),
            "runs_30d": runs,
            "children": [],
        }

    nodes = {s.capability: node(s) for s in specs}

    # A supervisor that is not itself registered gets a coordinator node.
    # Created from what the agents say about themselves, so a supervisor
    # nobody reports to any more simply stops existing.
    for spec in specs:
        boss = spec.supervisor
        if not boss or boss in known or boss in nodes:
            continue
        nodes[boss] = {
            "id": boss, "kind": "coordinator",
            "name": _title(boss), "role": KINDS["coordinator"],
            "domain": spec.domain, "supervisor": None, "version": None,
            "lifecycle": "active", "state": "idle", "health": "n/a",
            "runs_30d": 0, "children": [],
        }

    root = {
        "id": ROOT, "kind": "orchestrator", "name": "JARVIS",
        "role": KINDS["orchestrator"], "domain": None, "supervisor": None,
        "version": None, "lifecycle": "active", "state": "idle",
        "health": "n/a", "runs_30d": 0, "children": [],
    }

    for capability, item in nodes.items():
        boss = item.get("supervisor")
        parent = nodes.get(boss) if boss else None
        # An agent naming a supervisor that does not exist, or naming
        # itself, hangs off JARVIS rather than disappearing. A missing
        # node is invisible; a re-parented one is at least visible and
        # obviously odd.
        if parent is None or parent is item:
            root["children"].append(item)
        else:
            parent["children"].append(item)

    _sort(root)
    return {"root": root, "counts": _counts(root)}


def _title(capability: str) -> str:
    """A readable name for a node nobody registered a name for.

    The whole dotted id, not its last segment: "media.director" is the
    Media Director, and calling it "Director" loses the only word that
    says which one.
    """
    return capability.replace(".", " ").replace("_", " ").title()


def _sort(node: dict) -> None:
    """Coordinators first, then by name. Stable, so the tree does not
    reshuffle itself between two polls."""
    order = {"orchestrator": 0, "coordinator": 1, "governance": 2, "specialist": 3}
    node["children"].sort(key=lambda n: (order.get(n["kind"], 9), n["name"]))
    for child in node["children"]:
        _sort(child)


def _counts(node: dict, acc: dict | None = None) -> dict:
    acc = acc if acc is not None else {"agents": 0, "working": 0, "idle": 0,
                                       "waiting": 0, "queued": 0, "degraded": 0,
                                       "experimental": 0, "disabled": 0}
    for child in node["children"]:
        if child["kind"] != "coordinator":
            acc["agents"] += 1
            if child["state"] in acc:
                acc[child["state"]] += 1
            if child["lifecycle"] in acc:
                acc[child["lifecycle"]] += 1
        _counts(child, acc)
    return acc


# --- one agent, in full ----------------------------------------------------

async def detail(node_id: str, settings) -> dict | None:
    """Everything known about one node. Nothing that is not known.

    Where a figure has never been measured this returns None and says so,
    rather than a zero. A zero success rate and no runs at all look
    identical on a dashboard and mean opposite things.
    """
    if node_id == ROOT:
        return await _jarvis_detail(settings)

    spec = await registry.get(node_id)
    if spec is None:
        return await _coordinator_detail(node_id)

    return {
        "kind": _kind(spec),
        "identity": {
            "id": node_id,
            "agent_id": str(spec.id) if spec.id else None,
            "name": spec.name,
            "capability": spec.capability,
            "domain": spec.domain,
            "supervisor": spec.supervisor,
            "version": spec.version,
            "lifecycle": spec.status.value,
            "routable": spec.status in (Lifecycle.ACTIVE, Lifecycle.DEGRADED),
        },
        "role": spec.description,
        "responsibilities": _responsibilities(spec),
        "tools": list(spec.tools) or [],
        "models": _models(spec, settings) | {"recent": await _recent_model(node_id)},
        "permissions": _permissions(spec),
        "performance": await _performance(node_id),
        "economics": await _economics(node_id),
        "activity": await _activity(node_id),
        "limits": {
            "max_cost_inr": float(spec.max_cost_inr) if spec.max_cost_inr else None,
            "context_chars": spec.config.get("context_chars"),
            "scopes": spec.config.get("scopes") or [],
        },
    }


def _responsibilities(spec: AgentSpec) -> list[str]:
    """What it is actually on the hook for, from the registry row.

    Its task types are what the planner routes to it, so they are the
    honest answer to "what is this for" -- rather than a description
    somebody wrote once and never revisited.
    """
    out = []
    if spec.task_types:
        out.append("Takes work of type: " + ", ".join(spec.task_types))
    scopes = spec.config.get("scopes") or []
    if scopes:
        out.append("Sees context: " + ", ".join(scopes))
    else:
        out.append("Sees only its own task")
    if spec.tools:
        out.append("Uses: " + ", ".join(spec.tools))
    if spec.max_cost_inr:
        out.append(f"Will not spend more than Rs.{float(spec.max_cost_inr):.2f} on one task")
    return out


def _models(spec: AgentSpec, settings) -> dict:
    """Which tiers it may spend, and what those are today.

    A tier is what the agent asks for; the mapping to a vendor's model
    name lives in settings and changes when a provider retires a name.
    Showing both is the only way the answer stays true next month.
    """
    from app.agents import model_router

    allowed = []
    for tier in spec.model_tiers:
        provider, model = model_router._model_for(tier, settings)
        allowed.append({"tier": tier.value, "provider": provider, "model": model})
    default = allowed[0] if allowed else None
    return {"default": default, "allowed": allowed,
            "tiers": [t.value for t in spec.model_tiers]}


async def _recent_model(capability: str) -> dict | None:
    row = await fetchrow(
        """
        SELECT detail, created_at FROM agent_events
         WHERE capability = $1 AND kind = 'model_routed'
         ORDER BY created_at DESC LIMIT 1
        """,
        capability,
    )
    if row is None:
        return None
    detail = row["detail"] or {}
    return {"model": detail.get("model"), "tier": detail.get("tier"),
            "why": detail.get("why"), "when": row["created_at"]}


def _permissions(spec: AgentSpec) -> dict:
    """What it can and cannot do, every permission accounted for.

    Both halves matter. A list of what an agent holds tells you nothing
    about what it is prevented from doing, and "cannot publish" is the
    single most important fact about most of these agents.
    """
    can, cannot = [], []
    for perm, words in PERMISSION_WORDS.items():
        held = perm in spec.permissions
        entry = {"permission": perm.value, "what": words,
                 "needs_approval": perm in ALWAYS_APPROVED,
                 "never_delegated": perm in NEVER_DELEGATED}
        (can if held else cannot).append(entry)
    return {"can": can, "cannot": cannot,
            "held": sorted(p.value for p in spec.permissions)}


async def _performance(capability: str) -> dict:
    """How it has actually gone, over thirty days.

    Success, latency and confidence are measured on every run. Quality
    and correction rate are not measured anywhere yet, so they are absent
    rather than estimated -- a number nobody computes is worse than a
    blank, because a blank cannot be acted on by mistake.
    """
    rows = await fetch(
        """
        SELECT metric, count(*) AS n, avg(value) AS mean, sum(value) AS total
          FROM agent_metrics
         WHERE capability = $1 AND created_at > now() - interval '30 days'
         GROUP BY metric
        """,
        capability,
    )
    by = {r["metric"]: r for r in rows}

    def mean(metric):
        row = by.get(metric)
        return float(row["mean"]) if row and row["mean"] is not None else None

    def total(metric):
        row = by.get(metric)
        return int(row["total"]) if row and row["total"] is not None else 0

    runs = int((by.get("success") or {}).get("n") or 0)
    return {
        "days": 30,
        "runs": runs,
        "enough_to_judge": runs >= ENOUGH_RUNS,
        "success_rate": mean("success"),
        "failures": total("failure"),
        "refusals": total("refused"),
        "avg_confidence": mean("confidence"),
        # Recorded only on a successful run, so say which runs it covers.
        "avg_latency_ms": mean("latency_ms"),
        "avg_attempts": mean("attempts"),
        # Named so nobody reads a blank as a zero.
        "not_measured": ["correction rate", "quality score"],
    }


async def _economics(capability: str) -> dict:
    """Both cost figures, never added together."""
    row = await fetchrow(
        """
        SELECT
          COALESCE(SUM(spend_inr)  FILTER (WHERE finished_at >= date_trunc('day', now())), 0) AS spend_today,
          COALESCE(SUM(shadow_inr) FILTER (WHERE finished_at >= date_trunc('day', now())), 0) AS shadow_today,
          COALESCE(SUM(spend_inr)  FILTER (WHERE finished_at > now() - interval '30 days'), 0) AS spend_30d,
          COALESCE(SUM(shadow_inr) FILTER (WHERE finished_at > now() - interval '30 days'), 0) AS shadow_30d,
          count(*) FILTER (WHERE status = 'completed'
                             AND finished_at > now() - interval '30 days')                    AS done_30d
          FROM tasks WHERE capability = $1
        """,
        capability,
    )
    data = dict(row) if row else {}
    done = int(data.get("done_30d") or 0)
    spend30 = float(data.get("spend_30d") or 0)
    shadow30 = float(data.get("shadow_30d") or 0)
    return {
        "spend_today_inr": float(data.get("spend_today") or 0),
        "shadow_today_inr": float(data.get("shadow_today") or 0),
        "spend_30d_inr": spend30,
        "shadow_30d_inr": shadow30,
        "tasks_30d": done,
        "spend_per_task_inr": round(spend30 / done, 4) if done else None,
        "shadow_per_task_inr": round(shadow30 / done, 4) if done else None,
    }


async def _activity(capability: str) -> dict:
    """What it is doing, what is queued, and what it last did.

    Operational state only: objective, ids, timings, model, budget. Not
    what the agent was thinking -- that is neither recorded nor anybody's
    business, and a dashboard that displayed it would make it both.
    """
    running = await fetch(
        """
        SELECT t.id, t.objective, t.workflow_id, t.started_at, t.budget_cost,
               t.attempts, w.objective AS workflow_objective
          FROM tasks t LEFT JOIN workflows w ON w.id = t.workflow_id
         WHERE t.capability = $1 AND t.status = 'running'
         ORDER BY t.started_at DESC LIMIT 5
        """,
        capability,
    )
    current = []
    for row in running:
        model = await fetchrow(
            "SELECT detail FROM agent_events WHERE task_id = $1 "
            "AND kind = 'model_routed' ORDER BY created_at DESC LIMIT 1",
            row["id"],
        )
        current.append({
            "task_id": str(row["id"]),
            "objective": row["objective"],
            "workflow_id": str(row["workflow_id"]) if row["workflow_id"] else None,
            "workflow_objective": row["workflow_objective"],
            "started_at": row["started_at"],
            "budget_inr": float(row["budget_cost"]) if row["budget_cost"] else None,
            "attempt": row["attempts"],
            "model": ((model["detail"] if model else None) or {}).get("model"),
        })

    queued = await fetch(
        "SELECT id, objective, status FROM tasks WHERE capability = $1 "
        "AND status IN ('queued','blocked','waiting_approval') "
        "ORDER BY created_at LIMIT 10",
        capability,
    )
    recent = await fetch(
        """
        SELECT id, objective, status, confidence, spend_inr, shadow_inr,
               finished_at, failure_reason
          FROM tasks
         WHERE capability = $1 AND status IN ('completed','failed','cancelled')
         ORDER BY finished_at DESC NULLS LAST LIMIT 5
        """,
        capability,
    )
    rows = [dict(r) for r in recent]
    return {
        "current": current,
        "queued": [dict(r) for r in queued],
        "recent": rows,
        "last_failure": next(
            (r for r in rows if r["status"] == "failed"), None
        ),
    }


async def _coordinator_detail(node_id: str) -> dict | None:
    """A supervisor nobody registered: the domain summary.

    Says plainly that it is not an agent. A coordinator that looked like
    an agent here would be the beginning of the owner believing there is
    a model somewhere making these decisions, when what makes them is a
    graph and four gates.
    """
    reports = [s for s in await registry.find(routable_only=False)
               if s.supervisor == node_id]
    if not reports:
        return None

    capabilities = [s.capability for s in reports]
    live = await _live()
    record = await _record()

    row = await fetchrow(
        """
        SELECT count(*) FILTER (WHERE status = 'completed'
                                  AND finished_at >= date_trunc('day', now())) AS done_today,
               count(*) FILTER (WHERE status = 'failed'
                                  AND finished_at >= date_trunc('day', now())) AS failed_today,
               COALESCE(SUM(spend_inr)  FILTER (WHERE finished_at > now() - interval '30 days'), 0) AS spend_30d,
               COALESCE(SUM(shadow_inr) FILTER (WHERE finished_at > now() - interval '30 days'), 0) AS shadow_30d
          FROM tasks WHERE capability = ANY($1::text[])
        """,
        capabilities,
    )
    data = dict(row) if row else {}

    runs = sum(int((record.get(c) or {}).get("runs") or 0) for c in capabilities)
    weighted = sum(
        float((record.get(c) or {}).get("success_rate") or 0)
        * int((record.get(c) or {}).get("runs") or 0)
        for c in capabilities
    )

    states = [_state(live.get(c)) for c in capabilities]
    return {
        "kind": "coordinator",
        "identity": {
            "id": node_id, "name": _title(node_id),
            "domain": reports[0].domain, "supervisor": None,
            "lifecycle": "active", "agent_id": None, "version": None,
            "capability": node_id, "routable": False,
        },
        "role": (
            "Not an agent. A recipe and a set of gates: it decides what work "
            "a job needs, in what order, and what the results mean. It makes "
            "no model call and commands nobody — the orchestrator runs every "
            "step through the one execution path."
        ),
        "summary": {
            "agents": len(reports),
            "working": states.count("working"),
            "waiting": states.count("waiting"),
            "idle": states.count("idle"),
            "degraded": sum(1 for s in reports if s.status is Lifecycle.DEGRADED),
            "tasks_today": int(data.get("done_today") or 0),
            "failed_today": int(data.get("failed_today") or 0),
            "runs_30d": runs,
            "success_rate": round(weighted / runs, 3) if runs else None,
            "spend_30d_inr": float(data.get("spend_30d") or 0),
            "shadow_30d_inr": float(data.get("shadow_30d") or 0),
        },
        "reports": [{"id": s.capability, "name": s.name,
                     "lifecycle": s.status.value,
                     "state": _state(live.get(s.capability))} for s in reports],
    }


async def _jarvis_detail(settings) -> dict:
    """JARVIS itself: what the orchestrator is, and the whole board's numbers."""
    whole = await tree()
    row = await fetchrow(
        """
        SELECT count(*) FILTER (WHERE status = 'completed'
                                  AND finished_at >= date_trunc('day', now())) AS done_today,
               count(*) FILTER (WHERE status = 'running')                       AS running,
               COALESCE(SUM(spend_inr)  FILTER (WHERE finished_at > now() - interval '30 days'), 0) AS spend_30d,
               COALESCE(SUM(shadow_inr) FILTER (WHERE finished_at > now() - interval '30 days'), 0) AS shadow_30d
          FROM tasks
        """
    )
    data = dict(row) if row else {}
    return {
        "kind": "orchestrator",
        "identity": {
            "id": ROOT, "name": "JARVIS", "capability": None,
            "domain": None, "supervisor": "you", "version": None,
            "lifecycle": "active", "agent_id": None, "routable": False,
        },
        "role": (
            "The orchestrator. It plans what an objective needs, routes each "
            "step to a registered capability, and decides what the results "
            "amount to. It is not in the registry and holds no capability of "
            "its own: everything it does, it does by asking an agent."
        ),
        "summary": {
            **whole["counts"],
            "tasks_today": int(data.get("done_today") or 0),
            "running_now": int(data.get("running") or 0),
            "spend_30d_inr": float(data.get("spend_30d") or 0),
            "shadow_30d_inr": float(data.get("shadow_30d") or 0),
        },
        "reports": [{"id": c["id"], "name": c["name"], "kind": c["kind"],
                     "lifecycle": c["lifecycle"], "state": c["state"]}
                    for c in whole["root"]["children"]],
    }
