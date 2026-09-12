"""dev.plan -- what a brief would actually take, before anything is written.

Separate from writing the code, and for the same reason the media chain
separates deciding from drafting: something asked to write always writes.
A planner asked what a brief needs can answer "more than you think", or
"this names a file that does not exist", or "this part I cannot do" --
and those are the answers worth having before money is spent.

It reads the repository's own files to answer. A plan written without
looking at the code is a plan about an imagined codebase.
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

logger = logging.getLogger("jarvis.dev.plan")

# How many files one change may touch. Not a technical limit: a change
# spanning twenty files is one nobody reviews properly, and an unreviewed
# change is the thing this whole path exists to prevent.
MAX_FILES = 6

SPEC = AgentSpec(
    capability="dev.plan",
    name="Change planner",
    description=(
        "Reads a build brief against the actual repository and says what it "
        "would take: which files, in what order, what is risky, and which "
        "parts it cannot do. Writes no code."
    ),
    domain="dev",
    supervisor="dev.director",
    task_types=("dev", "planning"),
    permissions=frozenset({Permission.READ_FILES}),
    model_tiers=(ModelTier.DEEP, ModelTier.STANDARD),
    status=Lifecycle.ACTIVE,
    config={"scopes": ["working"], "context_chars": 20000},
    max_cost_inr=Decimal("15.00"),
)

_PROMPT = """\
You plan changes to a codebase you did not write. You decide WHAT would
have to change and in WHAT order. You write no code here.

The brief:
{brief}

The repository, as it actually is:
{tree}

{files}

Rules that are not stylistic:

- Name at most {limit} files. A change spanning more than that is one
  nobody reviews properly, and an unreviewed change is worse than no
  change. If the brief genuinely needs more, say so in `cannot` and plan
  the first useful slice instead.
- Only name files that exist in the listing above, or new files whose
  path follows the conventions you can see there.
- `cannot` is important and usually non-empty. Anything needing a
  credential, an account, a device, a decision only the owner can make,
  or a service that must be signed up for, goes there. Do not plan around
  it and do not invent it.
- `risk` is what could break that the brief did not mention.
- If the brief is not a request to change code at all -- it is notes, an
  article, a transcript -- set `is_a_change` to false and say what it is.

Reply with JSON only:
{{"is_a_change": true or false,
  "title": "a short branch-worthy name for this change",
  "summary": "two or three sentences on what this does",
  "files": [{{"path": "...", "why": "...", "new": true or false}}],
  "steps": ["..."],
  "risk": ["..."],
  "cannot": ["..."],
  "tests": "what would show this worked"}}
"""


async def run(handoff: Handoff, choice) -> _common.AgentResult:
    inputs = handoff.inputs or {}
    brief = str(inputs.get("brief") or handoff.objective or "")
    tree = str(inputs.get("tree") or "(the file listing was not available)")
    excerpts = str(inputs.get("files") or "")

    data, model = await _common.think(
        _PROMPT.format(brief=brief[:24000], tree=tree[:12000],
                       files=excerpts[:20000], limit=MAX_FILES),
        "dev.plan",
    )

    files = [f for f in (data.get("files") or [])
             if isinstance(f, dict) and str(f.get("path") or "").strip()][:MAX_FILES]
    cannot = [str(c) for c in (data.get("cannot") or []) if str(c).strip()]
    is_change = bool(data.get("is_a_change", True)) and bool(files)

    return _common.result(
        {"summary": str(data.get("summary") or ""),
         "is_a_change": is_change,
         "title": str(data.get("title") or "")[:200],
         "files": files,
         "steps": [str(s) for s in (data.get("steps") or [])],
         "risk": [str(r) for r in (data.get("risk") or [])],
         "cannot": cannot,
         "tests": str(data.get("tests") or "")},
        # A plan naming things it cannot do is a better plan, not a worse
        # one, so confidence does not fall for saying so. It falls when
        # nothing was named at all, because that is a plan about nothing.
        confidence=0.75 if is_change else 0.5,
        model=model,
        unresolved=cannot,
        next_action=("Write it" if is_change else "Nothing to build from this."),
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
