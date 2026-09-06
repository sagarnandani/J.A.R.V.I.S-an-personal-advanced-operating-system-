"""Which intelligence a job actually justifies.

Agents ask for a tier -- cheap, standard, deep -- and never for a model
name. That indirection is the whole point: providers rename and retire
models (this project has already been broken once by exactly that), and
when they do, one mapping changes instead of every agent.

The routing decision is deliberately explainable. It returns its reason
along with its choice, because "why did this cost so much?" is a question
the owner will ask, and "the router decided" is not an answer.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.agents.schemas import ModelTier


@dataclass(frozen=True)
class ModelChoice:
    provider: str
    model: str
    tier: ModelTier
    reason: str


def choose(
    settings,
    *,
    tier: ModelTier = ModelTier.STANDARD,
    allowed: tuple[ModelTier, ...] = (),
    budget_left: Decimal | None = None,
    risk: str = "normal",
) -> ModelChoice:
    """Pick a model for one task.

    Four inputs, in the order they can override each other:

      * the tier the task asks for;
      * the tiers the agent is permitted to spend (an agent registered
        for cheap work cannot quietly reach for the expensive model);
      * risk -- work that is costly to get wrong justifies more;
      * money left, which overrides everything. A tight budget drops the
        tier rather than failing the task, because a cheaper answer now
        beats no answer and an exhausted budget later.
    """
    reasons = []

    if risk == "high" and tier is not ModelTier.DEEP:
        tier = ModelTier.DEEP
        reasons.append("raised to deep: getting this wrong is expensive")

    if allowed and tier not in allowed:
        # Fall to the best tier this agent is actually allowed.
        order = [ModelTier.DEEP, ModelTier.STANDARD, ModelTier.CHEAP]
        for candidate in order:
            if candidate in allowed:
                reasons.append(
                    f"agent is not registered for {tier.value}; using {candidate.value}"
                )
                tier = candidate
                break

    if budget_left is not None and budget_left < Decimal("1") and tier is ModelTier.DEEP:
        tier = ModelTier.STANDARD
        reasons.append("dropped to standard: little budget left")

    provider, model = _model_for(tier, settings)
    if not reasons:
        reasons.append(f"{tier.value} is what the task asked for")

    return ModelChoice(provider=provider, model=model, tier=tier,
                       reason="; ".join(reasons))


def _model_for(tier: ModelTier, settings) -> tuple[str, str]:
    """The one place a tier becomes a vendor's model name.

    Every name comes from settings, so a retirement is an environment
    variable and a restart -- not a code change, and not an outage.
    """
    provider = settings.llm_provider.strip().lower()
    if provider == "claude":
        return "claude", settings.claude_model
    if provider == "mock":
        return "mock", "mock"
    return "gemini", {
        ModelTier.CHEAP: settings.model_cheap or settings.gemini_model,
        ModelTier.STANDARD: settings.gemini_model,
        ModelTier.DEEP: settings.model_deep or settings.gemini_model,
    }[tier]
