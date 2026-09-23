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
    measured=None,
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

    # What has actually happened, when the caller took the trouble to
    # find out. Deliberately a parameter rather than a query: this
    # function is synchronous and called on every routed task, and a
    # router that stalls on a statistics table is worse than one that
    # does not learn.
    #
    # Only ever escalates. "This model has been failing at this" is a
    # measurable claim; "that model would be better" is not -- it would
    # need the models tried on comparable work, which nothing here has
    # done. Pretending otherwise would be confident and wrong.
    if measured is not None and tier is not ModelTier.DEEP:
        tier = (ModelTier.STANDARD if tier is ModelTier.CHEAP
                else ModelTier.DEEP)
        reasons.append(
            f"raised to {tier.value}: {measured.model} has succeeded "
            f"{measured.success_rate:.0%} of the time here over "
            f"{measured.runs} runs"
        )

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
    if provider == "local":
        reasons.append(f"cheap work goes to {model} on your own machine, free")
    if not reasons:
        reasons.append(f"{tier.value} is what the task asked for")

    return ModelChoice(provider=provider, model=model, tier=tier,
                       reason="; ".join(reasons))


def _model_for(tier: ModelTier, settings) -> tuple[str, str]:
    """The one place a tier becomes a vendor's model name.

    Every name comes from settings, so a retirement is an environment
    variable and a restart -- not a code change, and not an outage.
    """
    # Sections 13 and 14: cheap work goes to the owner's own hardware
    # when he has some. Ahead of the configured provider on purpose --
    # configuring a local model IS the instruction to use it for this,
    # and it only ever applies to the cheap tier. Standard and deep work
    # never lands here: a 7B model on a home server is a real model with
    # real limits, and quietly answering a hard question with it is the
    # silent substitution the whole provider layer exists to prevent.
    if tier is ModelTier.CHEAP and getattr(settings, "local_llm_url", ""):
        return "local", settings.local_llm_model

    provider = settings.llm_provider.strip().lower()
    if provider == "claude":
        return "claude", settings.claude_model
    if provider == "openai":
        # Missing until now. OpenAI was added as a provider and the
        # router never learned the name, so every routed task fell
        # through to the Gemini branch below and was labelled "gemini"
        # whatever actually answered -- which would have made the
        # performance history that reads these labels quietly wrong.
        return "openai", settings.openai_model
    if provider == "mock":
        return "mock", "mock"
    return "gemini", {
        ModelTier.CHEAP: settings.model_cheap or settings.gemini_model,
        ModelTier.STANDARD: settings.gemini_model,
        ModelTier.DEEP: settings.model_deep or settings.gemini_model,
    }[tier]
