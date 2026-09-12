"""dev.patch -- writing one file, completely.

Whole files rather than unified diffs, and that is a deliberate trade. A
diff has to name exact line numbers and reproduce context byte for byte;
models get that subtly wrong often enough that half the patches fail to
apply, and a patch that fails to apply has cost money and produced
nothing. A whole file always applies. It costs more tokens and it cannot
half-succeed, which is the right way round for something the owner is
going to read as a diff anyway -- git works out the difference.

One file per call, on purpose. A single call rewriting six files runs out
of output tokens somewhere in the fourth and truncates it, and truncated
Python is a file that no longer parses.
"""
import logging
from decimal import Decimal

from app.agents import registry
from app.agents.schemas import (
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.media import _common

logger = logging.getLogger("jarvis.dev.patch")

SPEC = AgentSpec(
    capability="dev.patch",
    name="Change writer",
    description=(
        "Rewrites one file to carry out one step of an approved plan, in the "
        "style of the code around it. Writes into an isolated worktree, never "
        "the running deployment."
    ),
    domain="dev",
    supervisor="dev.director",
    task_types=("dev", "writing"),
    # Files, and only inside a worktree -- app/dev/repo.py resolves every
    # path and refuses anything outside it.
    permissions=frozenset({Permission.READ_FILES, Permission.WRITE_FILES}),
    model_tiers=(ModelTier.DEEP, ModelTier.STANDARD),
    status=Lifecycle.ACTIVE,
    config={"scopes": ["working"], "context_chars": 24000},
    max_cost_inr=Decimal("20.00"),
)

_PROMPT = """\
Write one file, completely.

The change being made:
{summary}

Why this file is part of it:
{why}

The plan's steps, for context. Do only the part that belongs in this file:
{steps}

{neighbours}

--- CURRENT CONTENT OF {path} ---
{current}
--- END ---

Rules:

- Return the ENTIRE file as it should be afterwards, not a fragment and
  not a diff. What you return replaces the file.
- Write like the code around it. Match its comment density, its naming,
  its imports, its error handling. A file that reads as though someone
  else wrote it is a file that gets rewritten later.
- Change as little as possible. Every line you touch is a line the owner
  has to read.
- Do not remove anything you were not asked to remove, including comments
  and tests.
- If this file should not change after all, say so instead of returning a
  cosmetic rewrite.

Reply with JSON only:
{{"changed": true or false,
  "why_not": "only if changed is false",
  "content": "the entire file",
  "note": "one sentence on what you did here"}}
"""


async def run(handoff: Handoff, choice) -> _common.AgentResult:
    inputs = handoff.inputs or {}
    path = str(inputs.get("path") or "")
    current = str(inputs.get("current") or "")

    data, model = await _common.think(
        _PROMPT.format(
            summary=str(inputs.get("summary") or handoff.objective or ""),
            why=str(inputs.get("why") or ""),
            steps=str(inputs.get("steps") or ""),
            neighbours=str(inputs.get("neighbours") or ""),
            path=path or "the file",
            current=current[:40000] or "(this file does not exist yet)",
        ),
        "dev.patch",
    )

    changed = bool(data.get("changed", True))
    content = data.get("content")
    if changed and not isinstance(content, str):
        changed = False

    # A rewrite that comes back dramatically shorter than what went in has
    # almost always been truncated or summarised rather than written. That
    # is not a judgement call worth leaving to the reviewer: truncated
    # Python does not parse, and it would fail the tests anyway with a
    # much more confusing message.
    if changed and current and len(content) < len(current) * 0.4:
        return _common.result(
            {"path": path, "changed": False,
             "why_not": (f"The rewrite came back at {len(content)} characters "
                         f"against {len(current)}, which means it was cut off "
                         f"rather than written. Left alone.")},
            confidence=0.2, model=model,
            unresolved=[f"{path} was not written: the rewrite looked truncated."],
            next_action="Try again, or narrow the step.",
        )

    return _common.result(
        {"path": path, "changed": changed,
         "content": content if changed else None,
         "why_not": str(data.get("why_not") or ""),
         "note": str(data.get("note") or "")},
        confidence=0.7 if changed else 0.6,
        model=model,
        next_action="Run the tests." if changed else "Nothing to write here.",
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
