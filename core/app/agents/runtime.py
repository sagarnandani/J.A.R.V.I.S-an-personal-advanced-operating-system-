"""Running one task through every layer, in order.

This is the only place an agent is actually invoked, which is what makes
the guarantees hold: there is no second path where the permission check
or the cost attribution could be forgotten.

    resolve agent -> check permissions -> check budget -> assemble context
    -> route model -> invoke -> charge -> record result -> measure

Failure handling lives here too, because "what to do when it goes wrong"
is part of running the task, not a separate concern bolted alongside it.
"""
import time
from decimal import Decimal
from uuid import UUID

from app.agents import context as ctx
from app.agents import cost, model_router, permissions, registry, tasks, telemetry
from app.agents.schemas import (
    AgentError,
    AgentResult,
    ApprovalRequired,
    BudgetExceeded,
    Handoff,
    ModelTier,
    Permission,
    PermissionDenied,
)
from app.config import get_settings

# Context scopes a capability gets by default. Anything more is declared
# by the agent, so widening what an agent can see is a visible change in
# the registry rather than an accident in a prompt.
DEFAULT_SCOPES = (ctx.Scope.WORKING,)


async def run_task(task_id: UUID) -> bool:
    """Execute one task. True if it completed.

    Every exit path writes the task's fate to the database before
    returning. A task that is running when the process dies is visible as
    'running' with a stale timestamp rather than vanishing.
    """
    settings = get_settings()
    task = await tasks.get(task_id)
    if task is None:
        return False

    workflow_id = task["workflow_id"]
    capability = task["capability"]
    started = time.perf_counter()

    spec = await registry.resolve(capability)
    if spec is None:
        await tasks.fail(task_id, f"No routable agent for '{capability}'.", terminal=True)
        await telemetry.record(
            "no_agent", workflow_id=workflow_id, task_id=task_id,
            capability=capability,
            detail={"reason": "no active version registered"},
        )
        return False

    await telemetry.record(
        "agent_selected", workflow_id=workflow_id, task_id=task_id,
        capability=capability,
        detail={
            "agent": str(spec.id), "version": spec.version, "status": spec.status.value,
            # Why this one -- the first question asked of any trace.
            "why": f"'{capability}' resolved to v{spec.version} ({spec.status.value})",
        },
    )

    if not await tasks.claim(task_id):
        return False  # somebody else has it

    try:
        needed = {Permission(p) for p in (task["constraints"] or {}).get("permissions", [])}
        for perm in needed:
            await permissions.require_approval(spec, perm, task_id)

        budget = task["budget_cost"] and Decimal(task["budget_cost"])
        estimate = budget or Decimal("0.5")
        if not await cost.affordable(workflow_id, estimate):
            raise BudgetExceeded(
                f"'{capability}' needs about Rs.{estimate} and the workflow "
                f"budget will not cover it."
            )

        scopes = tuple(
            ctx.Scope(s) for s in spec.config.get("scopes", [s.value for s in DEFAULT_SCOPES])
        )
        package = await ctx.assemble(
            task_id=task_id, objective=task["objective"], scopes=scopes,
            inputs=task["inputs"], settings=settings,
            agent_notes=spec.config.get("notes", ""),
            max_chars=int(spec.config.get("context_chars", 4000)),
        )
        await telemetry.record(
            "context_built", workflow_id=workflow_id, task_id=task_id,
            capability=capability,
            detail={"scopes": [s.value for s in package.scopes],
                    "chars": package.chars, "omitted": package.omitted},
        )

        choice = model_router.choose(
            settings,
            tier=ModelTier((task["constraints"] or {}).get("tier", "standard")),
            allowed=spec.model_tiers,
            budget_left=await cost.remaining(workflow_id),
            risk=(task["constraints"] or {}).get("risk", "normal"),
        )
        await telemetry.record(
            "model_routed", workflow_id=workflow_id, task_id=task_id,
            capability=capability,
            detail={"tier": choice.tier.value, "model": choice.model,
                    "provider": choice.provider, "why": choice.reason},
        )

        handoff = Handoff(
            task_id=task_id, workflow_id=workflow_id, objective=task["objective"],
            inputs=task["inputs"], context=package.text,
            constraints=task["constraints"] or {},
            expected_output=task["expected_output"] or "",
            budget_inr=budget, model_tier=choice.tier,
            permissions=spec.permissions,
        )

        fn = registry.implementation(capability)
        if fn is None:
            raise AgentError(
                f"'{capability}' is registered but has no implementation "
                f"loaded in this process.",
                retryable=False,
            )

        result: AgentResult = await fn(handoff, choice)

        spent = result.cost_inr or cost.price(
            result.tokens_in, result.tokens_out, choice.provider, settings
        )
        # What it would have cost on a paid model, recorded beside what it
        # actually cost. On a free tier the real figure is zero and true;
        # this is the one that makes workflows comparable.
        shadow = cost.shadow(
            result.tokens_in, result.tokens_out, choice.provider, settings
        )
        await cost.charge(task_id, workflow_id, spent, shadow)
        await tasks.complete(task_id, {"output": result.output,
                                       "evidence": result.evidence,
                                       "assumptions": result.assumptions,
                                       "unresolved": result.unresolved,
                                       "next_action": result.next_action},
                             confidence=result.confidence)

        elapsed = int((time.perf_counter() - started) * 1000)
        await telemetry.record(
            "task_completed", workflow_id=workflow_id, task_id=task_id,
            capability=capability, cost_inr=spent, duration_ms=elapsed,
            detail={"confidence": result.confidence, "model": choice.model,
                    "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                    "shadow_inr": float(shadow), "unresolved": result.unresolved},
        )
        await _measure(capability, spec.id, task_id, {
            "success": 1, "confidence": result.confidence,
            "latency_ms": elapsed, "cost_inr": float(spent),
            "shadow_inr": float(shadow),
            "attempts": task["attempts"] + 1,
        })
        return True

    except ApprovalRequired as exc:
        # Not a failure. The work is sound; it needs the owner.
        await tasks.await_approval(task_id, str(exc))
        await telemetry.record(
            "approval_required", workflow_id=workflow_id, task_id=task_id,
            capability=capability, detail={"category": exc.category, "reason": str(exc)},
        )
        return False

    except (PermissionDenied, BudgetExceeded) as exc:
        await tasks.fail(task_id, str(exc), terminal=True)
        await telemetry.record(
            "task_refused", workflow_id=workflow_id, task_id=task_id,
            capability=capability,
            detail={"reason": str(exc), "kind": type(exc).__name__},
        )
        await _measure(capability, spec.id, task_id, {"success": 0, "refused": 1})
        return False

    except Exception as exc:  # noqa: BLE001 - any agent failure is data
        retryable = getattr(exc, "retryable", True)
        await tasks.fail(task_id, str(exc), terminal=not retryable)
        after = await tasks.get(task_id)
        exhausted = after and after["status"] == "failed"
        await telemetry.record(
            "task_failed", workflow_id=workflow_id, task_id=task_id,
            capability=capability,
            detail={"reason": str(exc), "retryable": retryable,
                    "attempt": task["attempts"] + 1,
                    "will_retry": bool(retryable and not exhausted)},
        )
        await _measure(capability, spec.id, task_id, {
            "success": 0, "failure": 1, "attempts": task["attempts"] + 1,
        })
        return False


async def _measure(capability: str, agent_id, task_id: UUID, metrics: dict) -> None:
    """Record how it went. Generic on purpose.

    Metric name plus number, so a domain-specific measure later -- audience
    retention, say -- is a new row rather than a schema change.
    """
    from app.db import execute

    for metric, value in metrics.items():
        if value is None:
            continue
        try:
            await execute(
                "INSERT INTO agent_metrics (capability, agent_id, task_id, metric, value) "
                "VALUES ($1,$2,$3,$4,$5)",
                capability, agent_id, task_id, metric, Decimal(str(float(value))),
            )
        except Exception:  # noqa: BLE001 - measuring must not break the measured
            pass
