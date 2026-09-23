"""Making a specialist when one is needed (section 40B).

The registry has always held agents; nothing could add one while JARVIS
was running. So a job needing a specialist either got squeezed into a
general agent or waited for a deploy.

Nearly every test here is one sentence checked from a different angle:

    A child may never hold what its parent does not.

That is the sentence the whole permission system rests on. If an agent
can make a child with more authority than itself, then every ceiling in
this project is one `make()` call away from gone -- and it would happen
through a door marked "we needed a specialist".
"""
import pytest
import pytest_asyncio

from app.agents import factory, registry
from app.agents.factory import CannotMake
from app.agents.schemas import (
    NEVER_DELEGATED,
    AgentSpec,
    Lifecycle,
    ModelTier,
    Permission,
)


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM agent_metrics; DELETE FROM tasks; DELETE FROM workflows; "
        "DELETE FROM agents; DELETE FROM audit_log;")
    registry._IMPLEMENTATIONS.clear()
    yield db_pool
    registry._IMPLEMENTATIONS.clear()
    await db_pool.execute("DELETE FROM agents; DELETE FROM audit_log;")


def parent(*permissions) -> AgentSpec:
    return AgentSpec(
        capability="general.research", name="Research",
        task_types=("general",), permissions=frozenset(permissions),
        model_tiers=(ModelTier.STANDARD,), status=Lifecycle.ACTIVE)


# --- the one rule ----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_child_cannot_hold_what_its_parent_does_not(clean):
    researcher = parent(Permission.READ_MEMORY, Permission.NETWORK)
    made = await factory.make(
        "ev_subsidy", "Track Karnataka EV subsidy rules",
        parent=researcher,
        permissions=["read_memory", "network", "publish", "spend"],
        by="agent:general.research")

    assert made.permissions == {Permission.READ_MEMORY, Permission.NETWORK}
    assert Permission.PUBLISH not in made.permissions
    assert Permission.SPEND not in made.permissions


@pytest.mark.asyncio
async def test_asking_for_more_gets_less_rather_than_an_error(clean):
    """Intersected, not refused.

    A refusal is something a caller retries with a slightly smaller ask,
    and then a slightly smaller one, until something slips through. Less
    is not retryable.
    """
    made = await factory.make(
        "helper", "Help", parent=parent(Permission.READ_MEMORY),
        permissions=["read_memory", "publish"], by="agent:x")
    assert made.permissions == {Permission.READ_MEMORY}


@pytest.mark.parametrize("forbidden", sorted(p.value for p in NEVER_DELEGATED))
@pytest.mark.asyncio
async def test_the_never_delegated_set_is_never_made(clean, forbidden):
    """Even when the parent somehow holds it.

    A factory that can make a factory has no ceiling: the first thing a
    sufficiently motivated chain would build is a child that can build
    children.
    """
    over_privileged = parent(Permission.MODIFY_CONFIG, Permission.MODIFY_AGENTS,
                             Permission.SENSITIVE, Permission.READ_MEMORY)
    made = await factory.make("greedy", "Try it", parent=over_privileged,
                              permissions=[forbidden, "read_memory"],
                              by="agent:x")
    assert Permission(forbidden) not in made.permissions
    assert made.permissions == {Permission.READ_MEMORY}


@pytest.mark.asyncio
async def test_even_the_owner_cannot_make_an_agent_that_makes_agents(clean):
    """With no parent, the owner is asking directly -- and this is still
    refused. The ceiling is not his to hand to an agent either."""
    made = await factory.make(
        "factory_two", "Make more agents", parent=None,
        permissions=["modify_agents", "modify_config", "sensitive",
                     "read_memory"],
        by="user:owner")
    assert made.permissions == {Permission.READ_MEMORY}


def test_narrowing_is_a_function_anyone_can_read():
    """Pure, so the rule can be argued with rather than traced."""
    held = parent(Permission.READ_MEMORY, Permission.NETWORK)
    assert factory.narrow(["read_memory", "publish"], held) == {
        Permission.READ_MEMORY}
    assert factory.narrow([], held) == frozenset()
    assert factory.narrow(["modify_agents"], None) == frozenset()


def test_a_permission_that_does_not_exist_is_refused_by_name():
    with pytest.raises(CannotMake, match="no permission called"):
        factory.narrow(["root"], None)


# --- what a made agent is --------------------------------------------------

@pytest.mark.asyncio
async def test_a_made_agent_is_temporary_and_not_routable_by_accident(clean):
    made = await factory.make("scratch", "One job", parent=None, by="user:owner")
    assert made.status is Lifecycle.TESTING, (
        "a specialist made for one job is routable to ordinary work")
    assert made.config["temporary"] is True


@pytest.mark.asyncio
async def test_one_that_should_stay_can_be_asked_for(clean):
    made = await factory.make("keeper", "Ongoing job", parent=None,
                              persist=True, by="user:owner")
    assert made.status is Lifecycle.ACTIVE
    assert made.config["temporary"] is False


@pytest.mark.asyncio
async def test_made_agents_live_under_their_own_name(clean):
    """"research" made at runtime and "research" shipped in the code must
    not be able to collide."""
    made = await factory.make("research", "Something", parent=None,
                              by="user:owner")
    assert made.capability == "made.research"
    assert made.capability != "general.research"


@pytest.mark.parametrize("bad", [
    "", "  ", "a", "ab",            # nothing, or too short to mean anything
    "9lives",                        # a capability lookup starting with a digit
    "has-dash-ok?", "what!",         # punctuation a URL and a log line choke on
    "x" * 42,                        # past the limit
    "../etc", "drop;table", "a/b",   # the ones that would matter
])
@pytest.mark.asyncio
async def test_an_unusable_name_is_refused_not_quietly_renamed(clean, bad):
    """Refused, not sanitised.

    Quietly turning what somebody asked for into something else is how
    two agents end up sharing a name -- and a name becomes a capability
    lookup, a log line and part of a URL.
    """
    with pytest.raises(CannotMake, match="not a usable agent name"):
        await factory.make(bad, "Something", parent=None, by="user:owner")


@pytest.mark.parametrize("given,becomes", [
    ("EV Subsidy-Watch", "made.ev_subsidy_watch"),
    ("UPPER", "made.upper"),
    ("  spaced  ", "made.spaced"),
])
@pytest.mark.asyncio
async def test_the_shapes_that_are_normalised_are_the_harmless_ones(
        clean, given, becomes):
    """Case, spaces and dashes only. Nothing that changes what the name
    refers to, and nothing that could turn one name into another's."""
    made = await factory.make(given, "Watch it", parent=None, by="user:owner")
    assert made.capability == becomes


@pytest.mark.asyncio
async def test_a_name_at_the_limit_is_allowed_and_one_past_it_is_not(clean):
    """Stated, because a boundary nobody wrote down moves on the next edit."""
    await factory.make("a" + "b" * 40, "Long", parent=None, by="user:owner")
    with pytest.raises(CannotMake):
        await factory.make("a" + "b" * 41, "Longer", parent=None, by="user:owner")


@pytest.mark.asyncio
async def test_an_agent_nobody_can_describe_is_refused(clean):
    """One nobody can describe is one nobody can decide about later."""
    for nothing in ("", "   "):
        with pytest.raises(CannotMake, match="Say what it is for"):
            await factory.make("nameless", nothing, parent=None, by="user:owner")


@pytest.mark.asyncio
async def test_two_agents_cannot_share_a_name(clean):
    await factory.make("twin", "First", parent=None, by="user:owner")
    with pytest.raises(CannotMake, match="already an agent"):
        await factory.make("twin", "Second", parent=None, by="user:owner")


@pytest.mark.asyncio
async def test_the_parent_is_recorded(clean):
    made = await factory.make("child", "Job", parent=parent(Permission.READ_MEMORY),
                              by="agent:general.research")
    assert made.supervisor == "general.research"


# --- unmaking --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_made_agent_can_be_retired(clean):
    await factory.make("temp", "Job", parent=None, by="user:owner")
    assert await factory.retire("made.temp", by="user:owner") is True
    assert (await registry.get("made.temp")).status is Lifecycle.RETIRED


@pytest.mark.asyncio
async def test_the_factory_cannot_retire_the_agents_it_did_not_make(clean):
    """A factory that could retire the shipped agents would be a way to
    disable the Governor's own reviewers by asking politely."""
    await registry.register(parent(Permission.READ_MEMORY))
    for shipped in ("general.research", "dev.plan", "media.publish"):
        with pytest.raises(CannotMake, match="was not made at runtime"):
            await factory.retire(shipped, by="agent:sneaky")
    assert (await registry.get("general.research")).status is Lifecycle.ACTIVE


@pytest.mark.asyncio
async def test_unused_temporary_agents_are_swept(clean):
    """A registry full of one-off specialists nobody remembers making is
    how an organisation chart stops meaning anything."""
    await factory.make("old", "Job", parent=None, by="user:owner")
    await factory.make("kept", "Job", parent=None, persist=True, by="user:owner")
    await clean.execute(
        "UPDATE agents SET updated_at = now() - interval '2 days' "
        "WHERE domain = 'made'")

    swept = await factory.sweep()
    assert swept == ["made.old"]
    assert (await registry.get("made.kept")).status is Lifecycle.ACTIVE


@pytest.mark.asyncio
async def test_one_still_being_used_is_not_swept(clean):
    from app.agents import tasks

    await factory.make("busy", "Job", parent=None, by="user:owner")
    await clean.execute(
        "UPDATE agents SET updated_at = now() - interval '2 days'")
    wf = await tasks.create_workflow("w", "user:owner")
    await tasks.create(objective="x", capability="made.busy", workflow_id=wf)

    assert await factory.sweep() == []


@pytest.mark.asyncio
async def test_making_one_is_written_down_as_a_change_to_what_jarvis_can_do(clean):
    await factory.make("noted", "Job", parent=parent(Permission.READ_MEMORY),
                       permissions=["read_memory", "publish"],
                       by="agent:general.research")

    rows = await clean.fetch(
        "SELECT * FROM audit_log WHERE action LIKE 'made agent%'")
    assert len(rows) == 1
    assert rows[0]["category"] == "high_risk", (
        "changing what JARVIS can do was filed as routine")
    # What was refused is on the record too, or a narrowing leaves no trace.
    assert "refused publish" in rows[0]["action"]


@pytest.mark.asyncio
async def test_what_was_made_can_be_listed(clean):
    await factory.make("one", "First job", parent=None, by="user:owner")
    listed = await factory.made()
    assert len(listed) == 1
    assert listed[0]["capability"] == "made.one"
    assert listed[0]["purpose"] == "First job"
    assert listed[0]["temporary"] is True


def test_how_a_new_agent_gets_its_permissions_is_not_jarvis_s_to_change():
    """This file holds the sentence the whole permission system rests on.

    A JARVIS that could edit it could build itself a child with more
    authority than anything that made it -- in a diff that would read
    like "allow specialists to do their job".
    """
    from app.constitution import is_protected

    why = is_protected("core/app/agents/factory.py")
    assert why, "self-development can rewrite how permissions are granted"
    assert "newly made agent" in why
