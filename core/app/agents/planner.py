"""Turning an objective into a task graph, without being trusted to.

This is the seam the Agent Foundation left open. Until now a workflow's
steps were written by hand: the owner said "research this, then check
it", and named both. This module lets JARVIS work that out for itself.

It was deliberately built last, and it is deliberately fenced. A planner
is the one component that can invent work nobody asked for, so the useful
question is not "can it plan" but "what can a bad plan actually do". The
answer here is: very little, for four reasons.

**It can only name capabilities that already exist.** The catalogue comes
from the registry, and any step naming something not in it is refused.
There is no path from "the model wrote a word" to "code ran".

**It cannot widen anything.** A plan carries a capability, an objective
and an ordering. It does not carry permissions, budgets, model tiers or
constraints -- those come from the registry and the runtime, exactly as
they do for a hand-written step. A planner that could write its own
constraints could ask for approvals it should not, so it may not write
them at all.

**It is bounded before it runs.** A step cap, no cycles, no duplicate
names, no dependency on a step that does not exist. Each of those is
rejected rather than repaired, because quietly fixing a plan produces a
plan nobody wrote and nobody reviewed.

**It cannot fail into silence.** Every rejection is recorded, and a plan
that does not survive validation falls back to the single delegation the
system did before this module existed -- with the reason in the trace.

And planning is separate from executing. `propose()` returns a plan and
touches nothing, so a plan can be read before any of it happens.
"""
import json
import logging
import re
from dataclasses import dataclass, field

from app.agents import registry
from app.agents.schemas import Permission, Step
from app.config import get_settings

logger = logging.getLogger("jarvis.agents.planner")

# What a plan may name. Anything outside this is refused rather than
# mapped to the nearest thing -- guessing which capability was meant is
# how a plan ends up doing something adjacent to what was asked.
_ALLOWED_KEYS = {"name", "capability", "objective", "after", "expected_output"}


@dataclass
class Plan:
    """A proposal. Nothing has run and nothing has been created.

    `rejected` matters as much as `steps`. A planner that silently drops
    what it could not use looks like it agreed with you.
    """

    steps: list[Step] = field(default_factory=list)
    reasoning: str = ""
    rejected: list[str] = field(default_factory=list)
    source: str = "none"          # model | direct | none
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0

    def as_detail(self) -> dict:
        """The shape written to the trace, so a plan can be read back."""
        return {
            "source": self.source,
            "reasoning": self.reasoning[:1000],
            "steps": [
                {"name": s.name, "capability": s.capability,
                 "objective": s.objective, "after": list(s.after)}
                for s in self.steps
            ],
            "rejected": self.rejected,
            "model": self.model,
        }


_PROMPT = """\
You plan work for a personal AI operating system. You decide WHICH
capabilities run and IN WHAT ORDER. You never do the work yourself.

Capabilities available -- these are the only ones that exist:
{catalogue}

Rules:
- Use only the exact capability names listed above. Anything else is
  discarded, and the plan with it.
- At most {limit} steps. Fewer is better: every step costs real money on
  a small budget. One step is a perfectly good plan.
- Give each step a short name. `after` lists the names of the steps whose
  output this step needs -- leave it empty when the step can start
  immediately. Steps with no dependency on each other run at the same
  time, so only add an ordering you actually need.
- Write each step's objective so it stands alone. The agent running it
  sees its objective and the results of the steps it depends on, and
  nothing else -- not this plan, not the other steps.
- Do not invent a verification or writing step because it looks thorough.
  Add one when getting the answer wrong would matter, not by habit.
- If nothing listed can do this, return no steps and say why. That is a
  correct answer and a far better one than the nearest available thing.

Objective: {objective}

Reply with JSON only, no other text:
{{"reasoning": "one or two sentences on why this shape",
  "steps": [{{"name": "...", "capability": "...", "objective": "...",
             "after": [], "expected_output": "..."}}]}}
"""


def _plannable(specs):
    """The capabilities a plan may name.

    A capability that reports to a supervisor is a step in a chain that
    the supervisor assembles, not a free-standing thing to route to. The
    media chain is the case that made this necessary: `media.script` is
    registered with the writing task type, so a plan for "write something
    about X" could name it alone -- producing an uncited script with no
    research behind it, no verification, and no editorial review. Every
    gate that chain exists for, skipped, by a plan that looked reasonable.

    Chains are started by their coordinator with explicit steps, and
    explicit steps never come through here, so nothing is lost by
    excluding them.
    """
    return [s for s in specs if not s.supervisor]


def _catalogue(specs) -> str:
    """What the planner is allowed to know about its options.

    Description and task types, plus what each may touch. The permissions
    are shown so the plan is not built around something the agent would
    be refused for -- not as a menu to pick from: nothing here grants
    anything, and a plan cannot change what an agent holds.
    """
    lines = []
    for s in specs:
        touches = ", ".join(sorted(p.value for p in s.permissions)) or "nothing external"
        lines.append(
            f"- {s.capability}: {s.description or s.name}\n"
            f"    handles: {', '.join(s.task_types) or 'general'}; may touch: {touches}"
        )
    return "\n".join(lines)


def _parse(raw: str) -> tuple[list[dict], str]:
    """Read the model's JSON forgivingly, and never raise.

    Same rule as everywhere else in this project: a model that fences its
    JSON, prefaces it, or returns prose on a bad day must cost a fallback,
    not an exception on the owner's request.
    """
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("Planner did not return JSON; falling back.")
        return [], ""

    if not isinstance(data, dict):
        return [], ""
    steps = [s for s in (data.get("steps") or []) if isinstance(s, dict)]
    reasoning = data.get("reasoning") or ""
    return steps, reasoning if isinstance(reasoning, str) else ""


def _order(steps: list[Step]) -> list[str] | None:
    """Topological check. Returns the order, or None if there is a cycle.

    A cycle would not crash anything -- the runner would find nothing
    runnable and stop -- but it would look exactly like a stuck workflow,
    which is a far more expensive thing to debug than a refused plan.
    """
    pending = {s.name: set(s.after) for s in steps}
    done: list[str] = []
    while pending:
        ready = sorted(n for n, deps in pending.items() if not deps - set(done))
        if not ready:
            return None
        for name in ready:
            del pending[name]
        done.extend(ready)
    return done


def validate(raw_steps: list[dict], allowed: set[str], limit: int) -> tuple[list[Step], list[str]]:
    """Turn what the model wrote into steps, or say why it cannot be.

    Rejections are whole-plan on purpose wherever a partial repair would
    change the plan's meaning. Dropping one unknown dependency would let
    a checking step run before the thing it checks and still report
    success, which is worse than refusing outright.
    """
    rejected: list[str] = []

    if not raw_steps:
        return [], ["the plan contained no steps"]
    if len(raw_steps) > limit:
        return [], [f"the plan had {len(raw_steps)} steps and the limit is {limit}"]

    steps: list[Step] = []
    names: set[str] = set()
    for i, raw in enumerate(raw_steps):
        extra = set(raw) - _ALLOWED_KEYS
        if extra:
            # Not fatal, but recorded. The interesting case is a plan
            # trying to set its own constraints or budget, which is
            # exactly what a planner is not allowed to do.
            rejected.append(f"ignored fields on step {i + 1}: {', '.join(sorted(extra))}")

        capability = str(raw.get("capability") or "").strip()
        objective = str(raw.get("objective") or "").strip()
        name = str(raw.get("name") or f"step{i + 1}").strip()

        if capability not in allowed:
            return [], [f"'{capability or '(none)'}' is not a registered capability"]
        if not objective:
            return [], [f"step '{name}' had no objective"]
        if name in names:
            return [], [f"two steps are both called '{name}'"]
        names.add(name)

        after = raw.get("after") or []
        if not isinstance(after, list):
            return [], [f"step '{name}' has a malformed dependency list"]

        steps.append(Step(
            capability=capability, objective=objective, name=name,
            after=tuple(str(a).strip() for a in after if str(a).strip()),
            expected_output=str(raw.get("expected_output") or "").strip(),
        ))

    for step in steps:
        unknown = [a for a in step.after if a not in names]
        if unknown:
            return [], [f"step '{step.name}' depends on {unknown}, which is not in the plan"]

    if _order(steps) is None:
        return [], ["the plan's steps depend on each other in a loop"]

    return steps, rejected


async def _direct(objective: str) -> Plan:
    """The fallback that predates this module: hand it to one agent.

    Not a failure state. Most objectives genuinely are one step, and this
    is what the system did before a planner existed -- so falling back to
    it can never be worse than not having planned.
    """
    candidates = await registry.find(task_type="general")
    if not candidates:
        return Plan(source="none", reasoning="No routable capability can take this.")

    # Ranked, because the registry's own order is alphabetical and that is
    # not a decision. This fallback runs when nothing is known about the
    # objective, so it wants whichever agent can actually go and find out:
    # one with a tool, then one that may reach the network.
    #
    # An earlier version ranked by domain == "general" and picked a
    # capability that answers purely from training data -- so a question
    # about this week was answered from two years ago, with no sources and
    # no sign that nothing had been looked up. Preferring the general
    # domain sounds sensible and is exactly backwards: for an unclassified
    # question, being able to reach the world is the thing that matters.
    def rank(spec):
        return (0 if spec.tools else 1,
                0 if Permission.NETWORK in spec.permissions else 1,
                spec.capability)

    chosen = sorted(candidates, key=rank)[0]
    return Plan(
        steps=[Step(capability=chosen.capability, objective=objective,
                    name="direct")],
        source="direct",
        reasoning=f"Handed straight to {chosen.capability}.",
    )


async def propose(objective: str, *, max_steps: int | None = None) -> Plan:
    """Decide what work an objective requires. Creates nothing, runs nothing.

    Every path returns a Plan. Planning must not be able to fail the
    request it was trying to help with, so a model error, junk JSON or a
    plan that will not validate all land in the same place: the single
    delegation, with the reason recorded.
    """
    settings = get_settings()
    limit = max_steps or settings.planner_max_steps

    if not settings.planner_enabled:
        plan = await _direct(objective)
        plan.rejected.append("autonomous planning is switched off (PLANNER_ENABLED)")
        return plan

    specs = _plannable(await registry.find())
    if not specs:
        return Plan(source="none", reasoning="The registry has no routable agents.")

    from app.llm import get_provider

    try:
        result = await get_provider(settings).complete(
            _PROMPT.format(catalogue=_catalogue(specs), limit=limit,
                           objective=objective)
        )
    except Exception as exc:  # noqa: BLE001 - planning must not break the request
        logger.warning("Planning call failed: %s", exc)
        plan = await _direct(objective)
        plan.rejected.append(f"the planning call failed: {exc}")
        return plan

    raw_steps, reasoning = _parse(result.text)

    # An empty plan with a stated reason is a real answer, not a failure:
    # "nothing here can do this" beats picking the nearest capability and
    # producing something confident and irrelevant.
    if not raw_steps and reasoning:
        return Plan(source="model", reasoning=reasoning, model=result.model,
                    rejected=["the planner found no capability that fits"],
                    tokens_in=result.input_tokens, tokens_out=result.output_tokens)

    steps, rejected = validate(raw_steps, {s.capability for s in specs}, limit)
    if not steps:
        plan = await _direct(objective)
        plan.rejected = rejected + plan.rejected
        plan.model = result.model
        plan.tokens_in, plan.tokens_out = result.input_tokens, result.output_tokens
        return plan

    return Plan(steps=steps, reasoning=reasoning, rejected=rejected, source="model",
                model=result.model, tokens_in=result.input_tokens,
                tokens_out=result.output_tokens)
