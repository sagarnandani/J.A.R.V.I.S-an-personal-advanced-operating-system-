"""The registry: who exists, what they may do, which version is live.

The single source of truth JARVIS consults when deciding how to execute a
task. Everything about an agent that matters for routing lives here, so
routing never has to import agent code to ask a question about it.

Two rules the schema enforces rather than trusts:

* An agent is never edited in place. Changing behaviour means registering
  a new version. That is what makes "compare the new one against the old"
  and "roll back" possible at all, rather than being a thing you wish you
  had done afterwards.
* Only ACTIVE and DEGRADED versions are routable. Experimental work can
  sit in the registry indefinitely without any chance of being handed
  real work by accident.
"""
from decimal import Decimal
from uuid import UUID

from app.agents.schemas import (
    ROUTABLE,
    AgentSpec,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.db import execute, fetch, fetchrow

# The in-process half of the registry: capability -> callable.
#
# The database row says an agent may run; this says how to run it. Kept
# apart deliberately -- the row is durable state that outlives the
# process, the callable is code that ships with it. Storing a reference to
# code in the database would mean a deploy could leave rows pointing at
# functions that no longer exist.
_IMPLEMENTATIONS: dict[str, object] = {}


def implement(capability: str, fn) -> None:
    """Attach running code to a registered capability."""
    _IMPLEMENTATIONS[capability] = fn


def implementation(capability: str):
    return _IMPLEMENTATIONS.get(capability)


def _row_to_spec(row) -> AgentSpec:
    return AgentSpec(
        id=row["id"],
        capability=row["capability"],
        version=row["version"],
        name=row["name"],
        description=row["description"],
        domain=row["domain"],
        supervisor=row["supervisor"],
        task_types=tuple(row["task_types"]),
        tools=tuple(row["tools"]),
        permissions=frozenset(Permission(p) for p in row["permissions"]),
        model_tiers=tuple(ModelTier(t) for t in row["model_tiers"]),
        status=Lifecycle(row["status"]),
        max_cost_inr=row["max_cost_inr"],
        config=row["config"] or {},
    )


async def register(spec: AgentSpec) -> UUID:
    """Add a version of a capability.

    Registering the same capability and version twice updates the
    description and wiring but never the lifecycle status -- promoting an
    agent is a deliberate act, not something a redeploy does quietly.
    """
    row = await fetchrow(
        """
        INSERT INTO agents (capability, version, name, description, domain,
                            supervisor, task_types, tools, permissions,
                            model_tiers, status, max_cost_inr, config)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (capability, version) DO UPDATE SET
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            domain = EXCLUDED.domain,
            supervisor = EXCLUDED.supervisor,
            task_types = EXCLUDED.task_types,
            tools = EXCLUDED.tools,
            permissions = EXCLUDED.permissions,
            model_tiers = EXCLUDED.model_tiers,
            max_cost_inr = EXCLUDED.max_cost_inr,
            config = EXCLUDED.config,
            updated_at = now()
        RETURNING id
        """,
        spec.capability, spec.version, spec.name, spec.description, spec.domain,
        spec.supervisor, list(spec.task_types), list(spec.tools),
        [p.value for p in spec.permissions], [t.value for t in spec.model_tiers],
        spec.status.value, spec.max_cost_inr, spec.config,
    )
    assert row is not None
    return row["id"]


async def set_status(capability: str, version: int, status: Lifecycle) -> bool:
    """Move a version through its lifecycle.

    Activating a version stands the previous active one down in the same
    statement. Two live versions of one capability would make "which one
    answered?" unanswerable, which defeats the point of versioning.
    """
    if status is Lifecycle.ACTIVE:
        await execute(
            """
            UPDATE agents SET status = 'disabled', updated_at = now()
            WHERE capability = $1 AND version <> $2 AND status = 'active'
            """,
            capability, version,
        )
    result = await execute(
        "UPDATE agents SET status = $3, updated_at = now() "
        "WHERE capability = $1 AND version = $2",
        capability, version, status.value,
    )
    return result.endswith("1")


async def get(capability: str, version: int | None = None) -> AgentSpec | None:
    if version is not None:
        row = await fetchrow(
            "SELECT * FROM agents WHERE capability = $1 AND version = $2",
            capability, version,
        )
    else:
        row = await fetchrow(
            "SELECT * FROM agents WHERE capability = $1 "
            "ORDER BY version DESC LIMIT 1",
            capability,
        )
    return _row_to_spec(row) if row else None


async def resolve(capability: str) -> AgentSpec | None:
    """The version that should actually do the work right now.

    ACTIVE before DEGRADED, newest first. A degraded agent is still
    better than no agent -- refusing to run because the only version has
    a known wobble would turn a partial outage into a total one.
    """
    row = await fetchrow(
        """
        SELECT * FROM agents
        WHERE capability = $1 AND status = ANY($2::text[])
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, version DESC
        LIMIT 1
        """,
        capability, [s.value for s in ROUTABLE],
    )
    return _row_to_spec(row) if row else None


async def find(
    task_type: str | None = None,
    domain: str | None = None,
    routable_only: bool = True,
) -> list[AgentSpec]:
    """Search the registry the way the orchestrator does."""
    rows = await fetch(
        """
        SELECT DISTINCT ON (capability) *
        FROM agents
        WHERE ($1::text IS NULL OR $1 = ANY(task_types))
          AND ($2::text IS NULL OR domain = $2)
          AND (NOT $3 OR status = ANY($4::text[]))
        ORDER BY capability,
                 CASE status WHEN 'active' THEN 0 ELSE 1 END,
                 version DESC
        """,
        task_type, domain, routable_only, [s.value for s in ROUTABLE],
    )
    return [_row_to_spec(r) for r in rows]


async def versions(capability: str) -> list[AgentSpec]:
    """Every version, newest first -- for comparison and rollback."""
    rows = await fetch(
        "SELECT * FROM agents WHERE capability = $1 ORDER BY version DESC",
        capability,
    )
    return [_row_to_spec(r) for r in rows]
