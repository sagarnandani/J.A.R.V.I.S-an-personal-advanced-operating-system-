"""system.machine -- asking the owner's own server how it is doing.

The capability half of section 25. `app/computer.py` decides what may
run; this decides what to do with the answer, which is to put it in
front of a model fenced as output rather than as instructions, and let
it explain the numbers in the owner's terms.

It holds `run_command` and `read_memory` and nothing else. In particular
it does not hold `network`: a capability that can both run things on the
machine and reach the internet is one bug away from being a way to send
the machine's contents somewhere, and there is no reason this one needs
both.
"""
import logging
from decimal import Decimal

from app import computer
from app.agents import registry
from app.agents.schemas import (
    AgentResult,
    AgentSpec,
    Handoff,
    Lifecycle,
    ModelTier,
    Permission,
)
from app.config import get_settings
from app.llm import get_provider

logger = logging.getLogger("jarvis.capabilities.machine")

SPEC = AgentSpec(
    capability="system.machine",
    name="Check the machine",
    description=(
        "Looks at the server JARVIS runs on -- disk, memory, load, whether "
        "a service is up -- and says what it found in plain words."
    ),
    domain="system",
    task_types=("machine", "system", "server"),
    tools=("computer",),
    # No network. See the module docstring: running things and reaching
    # the internet do not belong in the same pair of hands.
    permissions=frozenset({Permission.RUN_COMMAND, Permission.READ_MEMORY}),
    model_tiers=(ModelTier.CHEAP, ModelTier.STANDARD),
    status=Lifecycle.ACTIVE,
    config={"scopes": ["working"], "context_chars": 800},
    max_cost_inr=Decimal("2.00"),
)

INSTRUCTION = """\
Below is the output of a command run on the owner's own server. Say what \
it means, in plain words, in a few short sentences.

Lead with the thing he would want to know -- a disk nearly full, a \
service that is down, memory almost gone. If nothing is wrong, say so \
in one line rather than reciting every number. Quote the actual figures \
where they matter. Do not guess at anything the output does not say.
"""


def _wanted(handoff: Handoff) -> tuple[str, dict, list]:
    """(action name, its values, or a raw command). Never guesses."""
    inputs = handoff.inputs or {}
    constraints = handoff.constraints or {}
    action = inputs.get("action") or constraints.get("action") or ""
    values = inputs.get("values") or constraints.get("values") or {}
    argv = inputs.get("argv") or constraints.get("argv") or []
    return str(action), dict(values), list(argv)


async def run(handoff: Handoff, choice) -> AgentResult:
    settings = get_settings()
    action, values, argv = _wanted(handoff)

    if action:
        ran = await computer.run_known(
            action, values, granted=handoff.permissions, settings=settings,
            actor=f"agent:{SPEC.capability}",
        )
    elif argv:
        # Not on the list. This raises ApprovalRequired the first time,
        # the task parks, and the owner sees the exact command. When he
        # says yes the task comes back through the same door and this
        # line runs -- against the argv he actually looked at.
        ran = await computer.run_anything(
            argv, granted=handoff.permissions, settings=settings,
            task_id=handoff.task_id, actor=f"agent:{SPEC.capability}",
            why=handoff.objective or "",
        )
    else:
        raise computer.CannotRun(
            "Nothing was named to run. I can check: "
            + ", ".join(a["name"] for a in computer.catalogue())
            + " -- or you can give me an exact command and I will show it "
            "to you before it runs."
        )

    provider = get_provider(settings)
    asked = handoff.objective or "How is the machine?"
    reply = await provider.complete(
        f"{INSTRUCTION}\nWhat the owner is asking: {asked}\n\n"
        f"{computer.as_material(ran)}"
    )

    unresolved = []
    if not ran.ok:
        unresolved.append(
            f"The command finished with exit code {ran.exit_code}, so what "
            f"it printed may be an error rather than an answer."
        )
    if ran.truncated:
        unresolved.append("The output was longer than I kept.")

    return AgentResult(
        output=reply.text,
        # What was read is a fact about the machine at that moment, which
        # is about as solid as this system gets -- but a non-zero exit
        # means the reading itself may not be one.
        confidence=0.85 if ran.ok else 0.4,
        evidence=[" ".join(ran.argv)],
        assumptions=[
            f"Ran on the server: {' '.join(ran.argv)}",
            ("You approved this command." if ran.asked_first
             else "This is one of the checks I may run without asking."),
        ],
        unresolved=unresolved,
        next_action="",
        model_used=reply.model or choice.model,
        tokens_in=reply.input_tokens,
        tokens_out=reply.output_tokens,
        cost_inr=Decimal(0),  # priced by the runtime, from one price list
    )


async def install() -> None:
    await registry.register(SPEC)
    registry.implement(SPEC.capability, run)
