"""How JARVIS speaks, and what it is allowed to claim.

These are prompt tests, which have a real limit worth stating: they check
the instruction JARVIS is given, not the reply it produces. Only the live
model decides that. What they do catch is the instruction being lost or
watered down in a later edit, which is how a persona quietly reverts.
"""
import pytest
import pytest_asyncio

from app.config import Settings
from app.llm.base import JARVIS_SYSTEM_PROMPT, system_prompt_with
from app.status import briefing


# --- the persona -----------------------------------------------------------

def test_jarvis_addresses_its_owner_as_sir():
    assert "sir" in JARVIS_SYSTEM_PROMPT.lower()


def test_it_is_told_to_greet_an_arrival():
    """'daddy's home' should get 'Welcome back, sir', not a help menu."""
    p = JARVIS_SYSTEM_PROMPT.lower()
    assert "daddy's home" in p
    assert "welcome back" in p


def test_the_chatbot_tics_are_named_and_banned():
    """Vague instructions to 'be concise' do not remove these.

    Naming the exact phrases is what stops them, so the test names them
    too -- otherwise a future edit softening this passes unnoticed.
    """
    p = JARVIS_SYSTEM_PROMPT
    assert "As an AI" in p
    assert "Certainly!" in p


def test_it_may_never_invent_a_figure():
    """The owner will ask about money. Money is where a guess does harm.

    A model handed a prompt with a number missing will supply a plausible
    one. This instruction is the only thing standing between that and a
    made-up income figure delivered in a confident voice.
    """
    p = JARVIS_SYSTEM_PROMPT
    assert "Never estimate, guess or illustrate a number" in p
    assert "not being tracked yet" in p


def test_language_mirroring_survived_the_rewrite():
    p = JARVIS_SYSTEM_PROMPT
    assert "Kannada" in p and "mix them back" in p


def test_memory_is_attached_as_notes_not_as_fact():
    """Facts are JARVIS's own summaries, and the owner outranks them."""
    built = system_prompt_with("- Owner's wife is called Sneha")
    assert "your own notes" in built
    assert "the owner is right" in built


# --- the status briefing ---------------------------------------------------

@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute("DELETE FROM memories; DELETE FROM audit_log;")
    yield db_pool
    await db_pool.execute("DELETE FROM memories; DELETE FROM audit_log;")


@pytest.mark.asyncio
async def test_the_briefing_reports_measured_numbers(clean):
    await clean.execute(
        "INSERT INTO audit_log (actor, action, category, outcome, cost) "
        "VALUES ('system','llm_message_exchange','low_risk','success',1.25)"
    )
    await clean.execute(
        "INSERT INTO memories (content, category, origin, confidence) "
        "VALUES ('Owner wife is called Sneha','people','inferred',0.8)"
    )

    text = await briefing(Settings(monthly_budget_inr=3500))

    assert "Exchanges today: 1" in text
    assert "Long-term facts held about the owner: 1" in text
    assert "Rs.1.25 of a Rs.3500 ceiling" in text


@pytest.mark.asyncio
async def test_spoken_exchanges_count_too(clean):
    """A conversation held aloud is still a conversation.

    Counting only typed messages would report a quiet day to an owner who
    had been talking to JARVIS all morning.
    """
    await clean.execute(
        "INSERT INTO audit_log (actor, action, category, outcome) "
        "VALUES ('user:x','llm_voice_exchange','low_risk','success')"
    )
    assert "Exchanges today: 1" in await briefing(Settings())


@pytest.mark.asyncio
async def test_it_says_plainly_what_is_not_tracked(clean):
    """The whole reason this module exists.

    Asked "how much did I make today?" with nothing in the prompt about
    income, a model invents a figure. Naming the gap is what prevents it,
    so the gap is named -- in the prompt, and here.
    """
    text = await briefing(Settings())
    # With nothing recorded, the gap is named rather than left blank --
    # a model handed a prompt with a missing figure supplies a plausible
    # one, and money is the subject where that costs most.
    assert "Money: nothing recorded yet" in text
    assert "never produce a figure" in text


@pytest.mark.asyncio
async def test_the_briefing_is_real_lines_not_escaped_text(clean):
    r"""It once rendered a literal backslash-n instead of line breaks.

    A prompt full of "\n" is still readable to a model, which is exactly
    why this would have gone unnoticed -- and why it is asserted rather
    than eyeballed.
    """
    text = await briefing(Settings())
    assert "\\n" not in text, "escaped newlines leaked into the prompt"
    assert text.count("\n") >= 5, "the briefing should be one line per figure"



@pytest.mark.asyncio
async def test_completed_work_is_counted_rather_than_denied(clean):
    """This line used to be a hardcoded "the task engine is not built".

    It went on being sent for a week after the task engine was built, so
    JARVIS said nothing was tracked while finished work sat in the
    database -- and a test asserted that it should. A claim about the
    system's own capabilities goes stale silently; a query cannot.
    """
    from app.agents import tasks

    wf = await tasks.create_workflow("a job", "user:owner")
    task_id = await tasks.create(objective="do it", capability="general.analysis",
                                 workflow_id=wf)
    await tasks.complete(task_id, {"output": "done"}, confidence=0.9)

    text = await briefing(Settings())
    assert "Tasks completed in the last 24 hours: 1" in text
    assert "NOT TRACKED" not in text.split("Money earned")[0], (
        "work that demonstrably happened was still reported as untracked"
    )


@pytest.mark.asyncio
async def test_finished_work_is_reported_once_and_then_not_again(clean):
    """A briefing that repeats yesterday's news is one you stop reading."""
    from app.agents import tasks
    from app.status import mark_seen

    wf = await tasks.create_workflow("check the subsidy", "schedule")
    task_id = await tasks.create(objective="check", capability="general.analysis",
                                 workflow_id=wf)
    await tasks.complete(task_id, {"output": "done"}, confidence=0.9)
    await tasks.set_workflow_status(wf, "completed")

    first = await briefing(Settings())
    assert "check the subsidy" in first

    await mark_seen()
    assert "check the subsidy" not in await briefing(Settings())


@pytest.mark.asyncio
async def test_a_piece_waiting_for_a_decision_reaches_the_briefing(clean):
    """The owner should not have to remember to open the Media tab.

    Only when something is actually waiting, though. A line saying
    "nothing is waiting" every single morning is how a briefing teaches
    its reader to skim past it.
    """
    quiet = await briefing(Settings())
    assert "Media waiting" not in quiet

    workflow = await clean.fetchval(
        "INSERT INTO workflows (objective, requested_by) "
        "VALUES ('Media: a topic','user:owner') RETURNING id")
    await clean.execute(
        "INSERT INTO content_pieces (workflow_id, topic, brand, state, title) "
        "VALUES ($1,'a topic','ai_media','ready','The benchmark everyone quotes')",
        workflow)
    try:
        text = await briefing(Settings())
        assert "Media waiting for the owner's decision: 1 piece(s)" in text
        assert "The benchmark everyone quotes" in text
        assert "Nothing has been published" in text
    finally:
        await clean.execute("DELETE FROM content_pieces; DELETE FROM workflows;")


@pytest.mark.asyncio
async def test_the_briefing_tells_jarvis_what_it_is_made_of(clean):
    """Asked about its own agents, JARVIS said it had none.

    It knew its spending and its schedules and nothing about itself, so
    it answered from training data. The roster reaches it the same way
    every other measured figure does.
    """
    from app.agents import registry
    from app.agents.schemas import AgentSpec, Lifecycle, ModelTier, Permission

    await clean.execute("DELETE FROM agents")
    await registry.register(AgentSpec(
        capability="research.web", name="Web research",
        description="Answers from live web sources.",
        task_types=("research",), permissions=frozenset({Permission.NETWORK}),
        model_tiers=(ModelTier.STANDARD,), status=Lifecycle.ACTIVE,
    ))
    try:
        text = await briefing(Settings())
        assert "research.web" in text
        assert "read from the agent registry" in text
        assert "not the same as having used it" in text
    finally:
        await clean.execute("DELETE FROM agents")


# --- rules added after JARVIS described work it had not done ---------------

def test_the_prompt_forbids_narrating_work_in_progress():
    """The first version of this rule named finished actions, and the
    model switched tense. "I'm fetching the latest" and "give me a
    minute" are the same lie in the present continuous."""
    from app.llm.base import JARVIS_SYSTEM_PROMPT as prompt

    lowered = prompt.lower()
    for phrase in ("searching", "fetching", "ask the owner to wait",
                   "media tab", "on screen"):
        assert phrase in lowered, f"nothing rules out {phrase!r}"


def test_the_prompt_forbids_drafting_the_content_in_the_reply():
    """He asked for a script and it wrote one in the chat window. An
    uncited draft with no research and no review is what the media chain
    exists to prevent, so writing one here defeats it."""
    from app.llm.base import JARVIS_SYSTEM_PROMPT as prompt

    assert "Do not write the content itself" in prompt
    assert "nobody reviewing it" in prompt


def test_the_spoken_prompt_says_an_accent_is_not_a_language():
    """He speaks English with an Indian accent and JARVIS answered in
    Kannada."""
    from app.routes.live import _VOICE_NOTE

    assert "An accent is not a language" in _VOICE_NOTE
    assert "English" in _VOICE_NOTE


@pytest.mark.asyncio
async def test_the_briefing_says_what_is_being_made_right_now(clean):
    """Asked why a script was taking so long, JARVIS invented an answer.

    Its notes carried finished work and work waiting on the owner, and
    nothing at all about work in flight -- so the one question it was
    being asked was the one thing it could not see.
    """
    quiet = await briefing(Settings())
    assert "Nothing is being made right now" in quiet

    workflow = await clean.fetchval(
        "INSERT INTO workflows (objective, requested_by, status) "
        "VALUES ('Media: a topic','user:owner','running') RETURNING id")
    await clean.execute(
        "INSERT INTO tasks (workflow_id, objective, capability, status) "
        "VALUES ($1,'research it','research.web','running')", workflow)
    await clean.execute(
        "INSERT INTO content_pieces (workflow_id, topic, brand, state) "
        "VALUES ($1,'the benchmark everyone quotes','ai_media','producing')",
        workflow)
    try:
        text = await briefing(Settings())
        assert "Media being made right now: 1 piece(s)" in text
        assert "the benchmark everyone quotes" in text
        assert "steps done" in text
        assert "started" in text, "no elapsed time, which is what he asked for"
        assert "say so plainly rather than explaining it away" in text
    finally:
        await clean.execute(
            "DELETE FROM content_pieces; DELETE FROM tasks; DELETE FROM workflows;")
