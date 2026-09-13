"""Taking a brief to a branch the owner can read.

The Director is a recipe and a set of gates, the same shape as the media
one and for the same reason: it invokes nothing itself. Every agent runs
through `runtime.run_task`, so permissions, budgets, cost and telemetry
hold here exactly as they do everywhere else.

The order is the whole design:

    plan  ->  is this even a change?  ->  worktree  ->  write  ->  test
          ->  commit  ->  a branch and a diff, waiting

and every one of those can stop. A brief that is not a change stops at
the first gate. A plan that says it cannot do the thing stops before a
worktree exists. A rewrite that came back truncated stops that file
rather than committing it. Failing tests do not stop the proposal -- the
owner may well want to see what it tried -- but they are reported as
failing and the change is never described as working.

What never happens: pushing, merging, and writing anywhere except inside
the worktree. See app/dev/repo.py, where those are absent rather than
guarded.
"""
import logging
from decimal import Decimal
from uuid import UUID

from app import governor
from app.agents import orchestrator, tasks
from app.dev import auditor, records, repo

logger = logging.getLogger("jarvis.dev.director")

# How much of the repository the planner is shown. Enough to see the
# shape and the conventions; not so much that listing it costs more than
# the change.
TREE_LIMIT = 400


async def _tree(worktree: str | None, settings) -> str:
    """What files exist, so the plan is about this codebase."""
    from pathlib import Path

    base = Path(worktree) if worktree else repo.root(settings)
    skip = {".git", "__pycache__", "node_modules", ".venv", ".jarvis-worktrees"}
    found = []
    for path in sorted(base.rglob("*")):
        if any(part in skip for part in path.parts):
            continue
        if path.is_file() and path.suffix in {
            ".py", ".js", ".mjs", ".html", ".sql", ".md", ".yaml", ".yml", ".txt"
        }:
            found.append(str(path.relative_to(base)))
        if len(found) >= TREE_LIMIT:
            found.append("... (listing truncated)")
            break
    return "\n".join(found)


async def begin(brief: str, title: str, requested_by: str,
                attachment_id=None, settings=None) -> dict:
    """Record the request and plan it. Writes nothing yet.

    Checked before any model call: a brief that becomes a plan and then
    discovers there is no git repository has spent money and the owner's
    attention for nothing.
    """
    ready = await repo.state(settings)
    request = await records.open_request(title or "Change", brief, attachment_id)

    if not ready["usable"]:
        await records.update(request["id"], state="failed", reason=ready["why"])
        return {**request, "state": "failed", "reason": ready["why"]}

    workflow_id = await orchestrator.start(
        f"Plan a change: {title or brief[:80]}", requested_by,
        steps=[
            orchestrator.Step(
                capability="dev.plan", name="plan",
                objective=f"Plan this change: {title or brief[:120]}",
                inputs={"brief": brief, "tree": await _tree(None, settings)},
                expected_output="Which files, in what order, and what it cannot do",
            )
        ],
    )
    await records.update(request["id"], workflow_id=workflow_id, state="planned")
    await orchestrator.advance(workflow_id)

    rows = await tasks.workflow_tasks(workflow_id)
    plan = _output(rows, "dev.plan")
    spent, shadow = _cost(rows)

    if not plan:
        reason = "The planner did not come back with anything usable."
        await records.update(request["id"], state="failed", reason=reason,
                             spend_inr=spent, shadow_inr=shadow)
        return {**request, "state": "failed", "reason": reason}

    if not plan.get("is_a_change"):
        # Not a failure. Most documents are not build briefs, and saying
        # so is the correct answer rather than a refusal.
        reason = f"This is not a change to build. {plan.get('summary', '')}"
        await records.update(request["id"], state="failed", reason=reason,
                             plan=plan, spend_inr=spent, shadow_inr=shadow)
        return {**request, "state": "failed", "reason": reason, "plan": plan}

    # What this change would be, before it is written. Classified here so
    # that a brief aimed at the protected core costs one planning call
    # and stops -- rather than a call per file and a branch nobody may
    # ever merge.
    paths = [str(f.get("path")) for f in (plan.get("files") or [])
             if isinstance(f, dict) and f.get("path")]
    risk = governor.classify(paths)

    if risk.level >= 4:
        why = ("This would change the protected core, which ordinary "
               "self-development never changes. " + " ".join(risk.reasons))
        await records.refuse(request["id"], why, risk=risk.as_detail())
        await records.update(request["id"], plan=plan, spend_inr=spent,
                             shadow_inr=shadow)
        return {**request, "state": "refused", "reason": why, "plan": plan,
                "risk": risk.as_detail()}

    await records.update(
        request["id"], plan=plan, spend_inr=spent, shadow_inr=shadow,
        risk=risk.as_detail(), risk_level=risk.level,
        title=(plan.get("title") or title or "Change")[:200],
    )
    return {**request, "state": "planned", "plan": plan,
            "risk": risk.as_detail(),
            "spend_inr": float(spent), "shadow_inr": float(shadow)}


async def build(request_id: UUID, requested_by: str, settings=None) -> dict:
    """Write the plan into a branch, run the tests, and stop there.

    Started separately from planning on purpose. A plan costs one call and
    can be read; writing costs one call per file and produces something
    the owner has to review. He decides whether it is worth it.
    """
    request = await records.get(request_id)
    if request is None:
        return {"state": "failed", "reason": "No such change request."}
    plan = request.get("plan") or {}
    files = [f for f in (plan.get("files") or []) if f.get("path")]
    if not files:
        return {"state": "failed", "reason": "That plan names no files."}

    # The commit this was written against. Needed to say later what
    # "going back" means, and cheap to record now rather than guess then.
    based_on = (await repo.state(settings)).get("head")
    await records.update(request_id, state="building", reason=None,
                         based_on=based_on)

    try:
        worktree = await repo.open_worktree(request["title"], settings)
    except repo.RepoError as exc:
        await records.update(request_id, state="failed", reason=str(exc))
        return {"state": "failed", "reason": str(exc)}

    try:
        return await _write(request, plan, files, worktree, requested_by, settings)
    except Exception as exc:  # noqa: BLE001 - a failed build is data
        logger.exception("Building a change failed")
        await records.update(request_id, state="failed", reason=str(exc),
                             branch=worktree["branch"])
        return {"state": "failed", "reason": str(exc),
                "branch": worktree["branch"]}
    finally:
        # The checkout goes; the branch stays. Removing the branch because
        # a temporary directory was cleaned up would throw away the thing
        # the owner was meant to read.
        await repo.close_worktree(worktree["path"], settings)


async def _write(request, plan, files, worktree, requested_by, settings) -> dict:
    """One model call per file, then the repository's own tests."""
    steps = []
    for i, spec in enumerate(files):
        path = str(spec["path"])
        current = await repo.read_file(worktree["path"], path)
        steps.append(orchestrator.Step(
            capability="dev.patch", name=f"file{i}",
            objective=f"Write {path}",
            inputs={"path": path, "current": current,
                    "summary": plan.get("summary", ""),
                    "why": spec.get("why", ""),
                    "steps": "\n".join(plan.get("steps") or []),
                    "neighbours": ""},
            expected_output="The whole file as it should be",
        ))

    workflow_id = await orchestrator.start(
        f"Write: {request['title']}", requested_by, steps=steps,
    )
    await orchestrator.advance(workflow_id)
    rows = await tasks.workflow_tasks(workflow_id)
    spent, shadow = _cost(rows)
    spent += Decimal(str(request.get("spend_inr") or 0))
    shadow += Decimal(str(request.get("shadow_inr") or 0))

    written, skipped = [], []
    for row in rows:
        out = (row.get("result") or {}).get("output")
        if not isinstance(out, dict) or not out.get("path"):
            continue
        if out.get("changed") and isinstance(out.get("content"), str):
            await repo.write_file(worktree["path"], out["path"], out["content"])
            written.append(out["path"])
        else:
            skipped.append(f"{out['path']}: {out.get('why_not') or 'left alone'}")

    if not written:
        reason = "Nothing was written. " + ("; ".join(skipped) or "")
        await records.update(request["id"], state="failed", reason=reason,
                             branch=worktree["branch"], workflow_id=workflow_id,
                             spend_inr=spent, shadow_inr=shadow)
        return {"state": "failed", "reason": reason, "branch": worktree["branch"]}

    # The repository's own tests, inside the worktree. A proposal with no
    # test result is not a proposal.
    tests = await repo.run_tests(worktree["path"])
    committed = await repo.commit(
        worktree["path"],
        f"{request['title']}\n\n{plan.get('summary', '')}\n\n"
        f"Proposed by JARVIS from a brief. Not reviewed.",
        settings,
    )

    if not committed["committed"]:
        reason = committed.get("why") or "Nothing changed on disk."
        await records.update(request["id"], state="failed", reason=reason,
                             branch=worktree["branch"], workflow_id=workflow_id,
                             spend_inr=spent, shadow_inr=shadow)
        return {"state": "failed", "reason": reason}

    built = (
        f"{len(committed['files'])} file(s) written on {worktree['branch']}. "
        + ("The tests pass." if tests.get("passed") is True
           else "The tests FAIL -- read them before merging."
           if tests.get("passed") is False
           else "The tests could not be run here.")
        + (f" Left alone: {'; '.join(skipped)}" if skipped else "")
    )

    # Everything below here is review. The branch already exists and
    # nothing that follows can change a line of it -- the worst case is
    # that it sits unapproved, which is the correct worst case.
    await records.update(
        request["id"], state="proposed", reason=built,
        branch=worktree["branch"], diff=committed["diff"],
        files_changed=len(committed["files"]),
        tests_passed=tests.get("passed"), tests_output=tests.get("output"),
        workflow_id=workflow_id, spend_inr=spent, shadow_inr=shadow,
    )

    verdict = await _review(request, plan, committed["diff"],
                            tests.get("passed"), spent, settings)

    return {
        "state": verdict["state"], "reason": verdict["reason"],
        "branch": worktree["branch"], "built": built,
        "files": committed["files"], "tests_passed": tests.get("passed"),
        "risk": verdict["risk"], "governor": verdict["governor"],
        "audit": verdict["audit"],
        "spend_inr": float(spent), "shadow_inr": float(shadow),
    }


async def _review(request, plan, diff, tests_passed, spent, settings) -> dict:
    """Audit the diff, then put it to the Governor.

    Runs on what was actually written, never on what was planned. The
    difference between those two is the single most useful thing an audit
    can find, and reviewing the plan would make it invisible by
    construction.

    A failure anywhere in here lands on "ask the owner". Review machinery
    that breaks must not be able to turn into approval -- and equally must
    not throw away a branch that was built correctly.
    """
    try:
        audit = await auditor.audit(
            brief=request.get("brief") or "", plan=plan, diff=diff,
            tests_passed=tests_passed, settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("The auditor failed")
        audit = {"blocking": [f"the auditor could not run: {exc}"],
                 "noted": [], "checked": "nothing -- the auditor failed"}

    paths = audit.get("files") or []
    try:
        decision = await governor.review(
            paths=paths, tests_passed=tests_passed, audit=audit,
            spend_inr=spent,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("The Governor could not decide")
        decision = governor.Decision(
            outcome="ask", level=3,
            why=f"The Governor could not reach a decision ({exc}), so this "
                f"is yours to judge.",
        )

    risk = decision.detail.get("risk") or governor.classify(paths).as_detail()
    record = decision.as_detail()

    if decision.outcome == "refused":
        await records.refuse(request["id"], decision.why,
                             governor=record, audit=audit, risk=risk)
        return {"state": "refused", "reason": decision.why, "risk": risk,
                "governor": record, "audit": audit}

    if decision.outcome == "autonomous":
        why = f"Approved by the Governor without asking. {decision.why}"
        await records.approved_by_governor(request["id"], why, record,
                                           audit, risk)
        return {"state": "approved", "reason": why, "risk": risk,
                "governor": record, "audit": audit}

    reason = f"Waiting for you. {decision.why}"
    await records.update(request["id"], state="proposed", reason=reason,
                         governor=record, audit=audit, risk=risk,
                         risk_level=risk.get("level"))
    return {"state": "proposed", "reason": reason, "risk": risk,
            "governor": record, "audit": audit}


def _output(rows, capability):
    for row in rows:
        if row["capability"] == capability and row["status"] == "completed":
            out = (row.get("result") or {}).get("output")
            if isinstance(out, dict):
                return out
    return None


def _cost(rows) -> tuple[Decimal, Decimal]:
    return (sum(Decimal(str(r["spend_inr"] or 0)) for r in rows),
            sum(Decimal(str(r["shadow_inr"] or 0)) for r in rows))
