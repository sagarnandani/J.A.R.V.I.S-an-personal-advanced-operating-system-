"""The smallest useful briefing for one task.

Explicitly NOT one shared memory every agent reads. Five scopes, and a
task is given only the ones its work requires:

    WORKING    what this task needs right now -- its inputs, and the
               results of the tasks it depends on
    AGENT      standing notes belonging to one capability
    PROJECT    knowledge belonging to an ongoing piece of work
    SHARED     verified facts several capabilities may need
    OWNER      long-term memory about the owner

Separation is not tidiness. A fact-checking agent handed the owner's
conversation history is slower, dearer, less accurate -- buried
instructions get missed -- and has been shown private information it had
no reason to see. Context isolation is a privacy control as much as a
cost one.

Assembly is budgeted in characters and fills in priority order, so an
overlong project note can never crowd out the actual objective.
"""
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from app.db import fetch


class Scope(str, Enum):
    WORKING = "working"
    AGENT = "agent"
    PROJECT = "project"
    SHARED = "shared"
    OWNER = "owner"


@dataclass
class ContextPackage:
    """What was assembled, and what was left out.

    `omitted` is reported rather than silently dropped: a result produced
    from a truncated briefing is not wrong, but knowing the briefing was
    truncated is the difference between diagnosing that and guessing.
    """

    text: str
    scopes: list[Scope] = field(default_factory=list)
    chars: int = 0
    omitted: list[str] = field(default_factory=list)


async def _dependency_results(task_id: UUID) -> list[str]:
    """What the tasks this one waited for actually produced.

    This is the working context that matters most: a task in a chain is
    almost always operating on the output of the task before it.
    """
    rows = await fetch(
        """
        SELECT t.capability, t.result
        FROM tasks t
        WHERE t.id = ANY(
            SELECT unnest(depends_on) FROM tasks WHERE id = $1
        ) AND t.result IS NOT NULL
        ORDER BY t.finished_at
        """,
        task_id,
    )
    out = []
    for r in rows:
        result = r["result"]
        if isinstance(result, dict):
            result = result.get("output", result)
        out.append(f"From {r['capability']}: {result}")
    return out


async def _owner_facts(query: str, limit: int, max_chars: int) -> list[str]:
    from app import facts

    return await facts.recall_facts(query, limit=limit, max_chars=max_chars)


async def assemble(
    *,
    task_id: UUID | None,
    objective: str,
    scopes: tuple[Scope, ...],
    inputs: dict | None = None,
    agent_notes: str = "",
    project_notes: str = "",
    shared_notes: str = "",
    max_chars: int = 4000,
    settings=None,
) -> ContextPackage:
    """Build the briefing, in priority order, within a character budget."""
    pkg = ContextPackage(text="", scopes=list(scopes))
    parts: list[str] = []
    left = max_chars

    def add(label: str, body: str, scope: Scope) -> None:
        nonlocal left
        if not body:
            return
        block = f"[{label}]\n{body}"
        if len(block) > left:
            pkg.omitted.append(f"{scope.value} (needed {len(block)}, had {left})")
            return
        parts.append(block)
        left -= len(block) + 2

    # Working context first, always. It is the only scope without which
    # the task cannot be done at all.
    if Scope.WORKING in scopes:
        if inputs:
            add("Inputs", "\n".join(f"- {k}: {v}" for k, v in inputs.items()), Scope.WORKING)
        if task_id is not None:
            deps = await _dependency_results(task_id)
            if deps:
                add("Results this task builds on", "\n".join(deps), Scope.WORKING)

    if Scope.AGENT in scopes:
        add("Notes for this capability", agent_notes, Scope.AGENT)
    if Scope.PROJECT in scopes:
        add("Project", project_notes, Scope.PROJECT)
    if Scope.SHARED in scopes:
        add("Verified shared knowledge", shared_notes, Scope.SHARED)

    # Owner memory last and only when asked for. Most capabilities have no
    # business seeing it, and it is the scope where leaking costs most.
    if Scope.OWNER in scopes and settings is not None:
        owner = await _owner_facts(objective, settings.memory_facts_limit, min(left, 1200))
        if owner:
            add("About the owner", "\n".join(f"- {f}" for f in owner), Scope.OWNER)

    pkg.text = "\n\n".join(parts)
    pkg.chars = len(pkg.text)
    return pkg
