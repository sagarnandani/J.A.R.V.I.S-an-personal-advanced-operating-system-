"""The Agent Foundation: the machinery future agents plug into.

Not agents. The things every agent needs so that adding one is a small
job rather than a new architecture each time:

    registry      who exists, what they may do, which version is live
    tasks         every piece of delegated work, never lost
    context       the smallest useful briefing for one task
    permissions   least privilege, and what needs the owner's approval
    model_router  which intelligence this job actually justifies
    cost          what it spent, attributed to task and workflow
    telemetry     what happened, replayable afterwards
    evaluation    how well it went
    runtime       executes one task through all of the above
    orchestrator  objective in, task graph out, results back

Deliberately a modular monolith. JARVIS is one person's system running on
a free container; queues, brokers and services would add operational
surface with nothing to show for it at this size. Every seam here is a
module boundary, so the day a piece needs to move out, it moves out --
`runtime` becoming a worker is the obvious first one.
"""
