"""Three minimal capabilities, to prove the foundation carries weight.

Deliberately generic and deliberately few. Their job is to exercise
registry, routing, context, permissions, cost and telemetry -- not to be
useful. The Media Company agents come after the foundation is trusted,
and building them now would mean debugging domain logic and plumbing at
the same time.

They do real model calls through the existing adapter, so what is being
proved is the real path rather than a simulation of it.
"""
from decimal import Decimal

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

SPECS = [
    AgentSpec(
        capability="general.research",
        name="Recall",
        # Named and described for what it actually is. It used to say it
        # "gathers" what is known, and to declare NETWORK it never used --
        # so a planner looking for something that could find things out
        # picked it, and the owner was told 2024 facts as though they had
        # just been looked up. A capability that overstates itself in the
        # registry is worse than one that does not exist.
        description=(
            "Answers from the model's own training data only. Reaches "
            "nothing, cites nothing, and knows nothing that happened after "
            "the model was trained. For anything current, or anything that "
            "needs a source, use research.web instead."
        ),
        domain="general",
        # Not "research". That word is research.web's, and sharing it is
        # how the wrong one got chosen.
        task_types=("general", "recall"),
        # No NETWORK. It never opened a connection in its life.
        permissions=frozenset({Permission.READ_MEMORY}),
        model_tiers=(ModelTier.STANDARD, ModelTier.DEEP),
        status=Lifecycle.ACTIVE,
        config={"scopes": ["working", "shared"]},
    ),
    AgentSpec(
        capability="general.analysis",
        name="Analysis",
        description="Draws conclusions from material it is given.",
        domain="general",
        task_types=("general", "analysis"),
        permissions=frozenset({Permission.READ_MEMORY}),
        model_tiers=(ModelTier.STANDARD, ModelTier.DEEP),
        status=Lifecycle.ACTIVE,
        config={"scopes": ["working"]},
    ),
    AgentSpec(
        capability="general.writer",
        name="Writer",
        description="Turns material into prose for a stated audience.",
        domain="general",
        task_types=("general", "writing"),
        # No PUBLISH. Writing something and putting it in front of the
        # world are different acts, and the second is the one that cannot
        # be taken back.
        permissions=frozenset({Permission.READ_MEMORY}),
        model_tiers=(ModelTier.CHEAP, ModelTier.STANDARD),
        status=Lifecycle.ACTIVE,
        config={"scopes": ["working", "owner"], "context_chars": 3000},
    ),
]

_INSTRUCTIONS = {
    "general.research": "Answer from what you already know. You have no "
                        "sources and no way to look anything up, so say "
                        "plainly when something may have changed since you "
                        "were trained, and never present recollection as a "
                        "finding.",
    "general.analysis": "Analyse the material provided. Give your conclusion "
                        "first, then the reasoning.",
    "general.writer": "Write the requested piece. Plain prose, no preamble.",
}


async def _think(handoff: Handoff, choice) -> AgentResult:
    """The shared body of all three: one model call, structured back.

    They differ by instruction, not by machinery. A capability that needed
    genuinely different machinery would get its own function -- this being
    shared is a statement that these three do not.
    """
    from app.llm import get_provider

    settings = get_settings()
    provider = get_provider(settings)

    prompt = (
        f"{_INSTRUCTIONS.get(handoff.objective, '')}\n\n"
        f"Objective: {handoff.objective}\n"
    )
    if handoff.expected_output:
        prompt += f"Expected output: {handoff.expected_output}\n"
    if handoff.context:
        prompt += f"\n{handoff.context}\n"

    result = await provider.complete(prompt)
    return AgentResult(
        output=result.text,
        # Honest rather than flattering: a single model call with no
        # verification step does not warrant certainty, and a made-up 0.95
        # would poison every downstream decision that reads it.
        confidence=0.7,
        model_used=result.model,
        tokens_in=result.input_tokens,
        tokens_out=result.output_tokens,
        cost_inr=Decimal(0),  # priced by the runtime, from one price list
    )


def _bind(capability: str):
    async def run(handoff: Handoff, choice) -> AgentResult:
        handoff = Handoff(**{**handoff.__dict__})
        prompt_prefix = _INSTRUCTIONS.get(capability, "")
        merged = Handoff(**{**handoff.__dict__,
                            "objective": f"{prompt_prefix}\n\n{handoff.objective}"})
        return await _think(merged, choice)

    return run


async def install() -> None:
    """Register every capability and attach its code.

    Idempotent: registering an existing version updates its description
    and leaves its lifecycle alone, so a restart never promotes or demotes
    anything by itself.
    """
    for spec in SPECS:
        await registry.register(spec)
        registry.implement(spec.capability, _bind(spec.capability))

    # Real capabilities, registered alongside the mocks. Each one is
    # installed separately so a failure in one does not take out the rest
    # -- a broken capability should be a missing capability, not a system
    # that will not start.
    from app.agents.capabilities import (
        browse_page, factcheck_claims, machine, read_page, research_web,
    )
    from app.dev import patch as dev_patch
    from app.dev import plan as dev_plan
    from app.agents.capabilities import publish as publish_cap
    from app.media import review, script, scout, strategy

    for module in (research_web, read_page, browse_page, machine,
                   factcheck_claims,
                   scout, strategy, script, review,
                   dev_plan, dev_patch, publish_cap):
        try:
            await module.install()
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("jarvis.agents").warning(
                "Could not install %s: %s", module.__name__, exc
            )
