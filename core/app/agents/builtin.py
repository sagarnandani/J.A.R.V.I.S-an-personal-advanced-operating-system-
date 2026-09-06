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
        name="Research",
        description="Gathers and summarises what is known about a question.",
        domain="general",
        task_types=("general", "research"),
        permissions=frozenset({Permission.READ_MEMORY, Permission.NETWORK}),
        # Research is broad but rarely subtle: standard is the right
        # default, deep is available when getting it wrong is expensive.
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
    "general.research": "Research the objective. State what is established, "
                        "what is contested, and what you could not determine.",
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
    """Register the built-in capabilities and attach their code.

    Idempotent: registering an existing version updates its description
    and leaves its lifecycle alone, so a restart never promotes or demotes
    anything by itself.
    """
    for spec in SPECS:
        await registry.register(spec)
        registry.implement(spec.capability, _bind(spec.capability))
