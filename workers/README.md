# Workers

Empty on purpose. This directory is a placeholder for Stage 1's background
worker (architecture doc, section D/K: long-running jobs — research,
content generation, scheduled checks — that keep running whether or not a
device has the app open).

Nothing here yet because Stage 0 has no task engine and no agents for a
worker to execute — building a worker before there's anything for it to
do would be infrastructure with no payoff, which the architecture doc
explicitly calls out as a risk to avoid (section N, "Overengineering
risk").

Do not proceed to Stage 1 until Stage 0's Definition of Done is complete
and confirmed — see `/docs/STAGE0_STATUS.md`.
