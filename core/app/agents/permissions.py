"""Least privilege, and what the owner must be asked about.

Two separate questions, deliberately not merged:

    May this agent do this at all?   -- the grant
    Should the owner be asked first? -- the policy

An agent can hold PUBLISH and still not publish without approval. Merging
them would mean the only way to require approval is to withhold the
permission, and then the agent cannot even ask.
"""
from app.agents.schemas import (
    ALWAYS_APPROVED,
    NEVER_DELEGATED,
    AgentSpec,
    ApprovalRequired,
    Permission,
    PermissionDenied,
)
from app.db import fetchrow


def granted(spec: AgentSpec, needed: Permission) -> bool:
    return needed in spec.permissions


def check(spec: AgentSpec, needed: Permission) -> None:
    """Raise unless this agent may do this. Never returns a bool.

    A boolean invites `if allowed(...)` written the wrong way round once,
    somewhere, quietly. Raising means the failure mode of forgetting to
    check is that nothing is checked -- loud in a test -- rather than
    everything being permitted.
    """
    if needed in NEVER_DELEGATED:
        raise PermissionDenied(
            f"'{needed.value}' is never delegated to an agent. "
            f"Only the owner changes how JARVIS itself works."
        )
    if needed not in spec.permissions:
        raise PermissionDenied(
            f"Agent '{spec.capability}' does not hold '{needed.value}'."
        )


async def policy_for(category: str) -> str:
    """What the owner decided about this category of action, from Stage 0."""
    row = await fetchrow(
        "SELECT default_policy FROM approvals WHERE action_category = $1",
        category,
    )
    return row["default_policy"] if row else "ask_every_time"


async def require_approval(
    spec: AgentSpec, needed: Permission, task_id=None
) -> None:
    """Raise ApprovalRequired if the owner has to be asked first.

    Unknown categories default to asking. A permission introduced later
    with no policy row should stop and ask rather than proceed silently,
    which is the safe direction to be wrong in.

    `task_id` is what makes a resumed task able to get past here. The
    approval is recorded against that exact task, so answering yes to one
    post is not answering yes to publishing -- and without the id, a
    resumed task would stop at the same gate for ever.
    """
    from app.agents import approvals

    check(spec, needed)
    category = ALWAYS_APPROVED.get(needed)
    if category is None:
        return
    if await approvals.granted(task_id, category):
        return
    if await policy_for(category) != "auto":
        raise ApprovalRequired(
            f"'{needed.value}' needs your approval before "
            f"'{spec.capability}' may do it.",
            category=category,
        )


def escalate(spec: AgentSpec, requested: frozenset[Permission]) -> frozenset[Permission]:
    """The permissions an agent may pass to work it delegates onwards.

    Never more than it holds itself. Without this, a supervisor with broad
    rights could hand them to a specialist that was deliberately given
    fewer -- and the narrow grant would be decorative.
    """
    return frozenset(requested) & spec.permissions
