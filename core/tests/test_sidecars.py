"""Eyes and hands on the owner's other machines (sections 19 and 40C).

The server is the brain and has no screen. A sidecar is a small program
on his Mac or PC offering a named, limited set of things it will do.

Almost everything here is about one sentence: **a job has to survive
three narrowings.** What the owner granted that machine, what the agent
asking holds, and what the action needs. A test that only checked one of
them would pass on a system where any agent could use any machine.

The other thing worth proving is the direction of the connection. The
sidecar asks for work; nothing here dials into his laptop. That is what
makes closing the lid an off switch nothing on the server can override.
"""
from uuid import uuid4

import pytest
import pytest_asyncio

from app import sidecars
from app.agents.schemas import Permission
from app.sidecars import CAPABILITIES, NEEDS, Sidecar, SidecarError

ALL = frozenset(CAPABILITIES)
# An agent that holds everything, so a refusal in these tests is always
# the SIDECAR's doing rather than the agent's.
POWERFUL = frozenset(Permission)


@pytest_asyncio.fixture
async def clean(db_pool):
    await db_pool.execute(
        "DELETE FROM sidecar_jobs; DELETE FROM sidecars; "
        "DELETE FROM sidecar_pairings; DELETE FROM audit_log;")
    yield db_pool
    await db_pool.execute(
        "DELETE FROM sidecar_jobs; DELETE FROM sidecars; "
        "DELETE FROM sidecar_pairings; DELETE FROM audit_log;")


def a_sidecar(capabilities=("screenshot",), status="active") -> Sidecar:
    return Sidecar(id=uuid4(), name="Mac", machine="Darwin",
                   capabilities=frozenset(capabilities), status=status,
                   last_seen_at=None)


async def paired(capabilities=("screenshot",), name="Mac"):
    """A real pairing, through the real path, so the test uses the door."""
    offer = await sidecars.offer_pairing(name, capabilities, by="user:owner")
    got = await sidecars.redeem_pairing(offer["code"], {"machine": "Darwin 23"})
    found = await sidecars.authenticate(got["token"])
    return found, got["token"]


# --- the three narrowings --------------------------------------------------

def test_a_machine_cannot_do_what_it_was_not_granted():
    mac = a_sidecar(["screenshot"])
    allowed, why = sidecars.may_run(mac, "terminal", POWERFUL)
    assert allowed is False
    assert "was not given 'terminal'" in why
    assert "screenshot" in why, "the refusal does not say what it CAN do"


def test_an_agent_cannot_use_a_capability_it_has_no_permission_for():
    """The machine allowing it is not the agent being allowed to ask."""
    mac = a_sidecar(ALL)
    allowed, why = sidecars.may_run(
        mac, "terminal", frozenset({Permission.READ_MEMORY}))
    assert allowed is False
    assert "run_command" in why


def test_both_together_is_what_lets_it_through():
    mac = a_sidecar(["terminal"])
    assert sidecars.may_run(mac, "terminal",
                            frozenset({Permission.RUN_COMMAND}))[0] is True


@pytest.mark.parametrize("capability,permission", sorted(NEEDS.items()))
def test_every_capability_is_behind_a_permission(capability, permission):
    """No capability is free. If one were, granting a machine would be
    the only check, and the agent asking would not be checked at all."""
    mac = a_sidecar([capability])
    assert sidecars.may_run(mac, capability, frozenset())[0] is False
    assert sidecars.may_run(mac, capability, frozenset({permission}))[0] is True


def test_a_capability_nobody_wrote_down_does_not_exist():
    mac = a_sidecar(ALL)
    for invented in ("root", "ssh", "sudo", "", "screenshot; rm -rf /"):
        assert sidecars.may_run(mac, invented, POWERFUL)[0] is False


def test_a_paused_machine_does_nothing():
    for standing in ("paused", "revoked"):
        mac = a_sidecar(ALL, status=standing)
        allowed, why = sidecars.may_run(mac, "screenshot", POWERFUL)
        assert allowed is False and standing in why


def test_the_dangerous_ones_ask_every_time():
    """A grant says the owner is willing for the machine to be ABLE to.
    It is not a standing yes for it to happen now, unwatched."""
    assert sidecars.needs_approval("terminal") is True
    assert sidecars.needs_approval("files.write") is True
    assert sidecars.needs_approval("screenshot") is False


# --- pairing ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_machine_gets_exactly_what_the_owner_chose(clean):
    mac, _ = await paired(["screenshot", "notify"])
    assert mac.capabilities == {"screenshot", "notify"}


@pytest.mark.asyncio
async def test_a_machine_cannot_ask_for_capabilities(clean):
    """The thing a compromised sidecar would try first.

    What it reports about itself is descriptive. If anything it said
    could widen it, pairing would be a request rather than a grant.
    """
    offer = await sidecars.offer_pairing("Mac", ["screenshot"], by="user:owner")
    got = await sidecars.redeem_pairing(offer["code"], {
        "machine": "Darwin",
        "capabilities": ["terminal", "files.write"],
        "status": "active", "admin": True,
    })
    assert got["sidecar"]["capabilities"] == ["screenshot"]

    mac = await sidecars.authenticate(got["token"])
    assert mac.capabilities == {"screenshot"}
    assert sidecars.may_run(mac, "terminal", POWERFUL)[0] is False


@pytest.mark.asyncio
async def test_a_pairing_code_works_once(clean):
    offer = await sidecars.offer_pairing("Mac", ["screenshot"], by="user:owner")
    await sidecars.redeem_pairing(offer["code"])
    with pytest.raises(SidecarError, match="already used"):
        await sidecars.redeem_pairing(offer["code"])


@pytest.mark.asyncio
async def test_a_made_up_code_pairs_nothing(clean):
    for guess in ("AAAA-BBBB-CCCC", "", "guess"):
        with pytest.raises(SidecarError):
            await sidecars.redeem_pairing(guess)


@pytest.mark.asyncio
async def test_an_expired_code_pairs_nothing(clean):
    offer = await sidecars.offer_pairing("Mac", ["screenshot"], by="user:owner")
    await clean.execute(
        "UPDATE sidecar_pairings SET expires_at = now() - interval '1 minute'")
    with pytest.raises(SidecarError, match="fifteen minutes"):
        await sidecars.redeem_pairing(offer["code"])


@pytest.mark.asyncio
async def test_the_token_is_never_stored_where_it_could_be_read_back(clean):
    """He gets it once. If it could be read back, every future compromise
    of the dashboard would hand over every machine he owns."""
    _, token = await paired()
    row = await clean.fetchrow("SELECT * FROM sidecars")
    stored = " ".join(str(v) for v in row.values())
    assert token not in stored
    assert token[:12] not in stored


@pytest.mark.asyncio
async def test_a_pairing_with_no_capabilities_is_refused(clean):
    with pytest.raises(SidecarError, match="can do nothing"):
        await sidecars.offer_pairing("Mac", [], by="user:owner")


@pytest.mark.asyncio
async def test_a_capability_that_does_not_exist_is_refused_at_pairing(clean):
    with pytest.raises(SidecarError, match="no such capability"):
        await sidecars.offer_pairing("Mac", ["screenshot", "sudo"], by="user:owner")


@pytest.mark.asyncio
async def test_a_wrong_token_is_nobody(clean):
    await paired()
    assert await sidecars.authenticate("not-a-token") is None
    assert await sidecars.authenticate("") is None


# --- work ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_job_is_queued_and_handed_over_when_asked_for(clean):
    mac, _ = await paired(["screenshot"])
    await sidecars.queue(mac, "screenshot", "take", agent_holds=POWERFUL)

    jobs = await sidecars.next_jobs(mac)
    assert len(jobs) == 1 and jobs[0]["capability"] == "screenshot"

    # Handed out once. Two copies of the same sidecar must not both take it.
    assert await sidecars.next_jobs(mac) == []


@pytest.mark.asyncio
async def test_a_job_that_could_never_run_is_refused_not_queued(clean):
    """A job sitting in a queue until it expires, for a reason knowable
    when it was written, is a failure nobody sees."""
    mac, _ = await paired(["screenshot"])
    with pytest.raises(SidecarError, match="was not given 'terminal'"):
        await sidecars.queue(mac, "terminal", "run", agent_holds=POWERFUL)
    assert await clean.fetchval("SELECT count(*) FROM sidecar_jobs") == 0


@pytest.mark.asyncio
async def test_a_dangerous_job_waits_for_the_owner(clean):
    mac, _ = await paired(["terminal"])
    await sidecars.queue(mac, "terminal", "run", {"argv": ["ls"]},
                         agent_holds=POWERFUL)

    assert await sidecars.next_jobs(mac) == [], "it ran without being approved"
    assert len(await sidecars.waiting_for_owner()) == 1


@pytest.mark.asyncio
async def test_approving_one_job_releases_that_job(clean):
    mac, _ = await paired(["terminal"])
    job = await sidecars.queue(mac, "terminal", "run", {"argv": ["ls"]},
                               agent_holds=POWERFUL)
    assert await sidecars.approve(job["id"], by="user:owner") is True

    jobs = await sidecars.next_jobs(mac)
    assert len(jobs) == 1 and jobs[0]["id"] == job["id"]


@pytest.mark.asyncio
async def test_approving_one_job_does_not_release_the_next(clean):
    """The quiet failure. One yes is one job, never a category."""
    mac, _ = await paired(["terminal"])
    first = await sidecars.queue(mac, "terminal", "run", {"argv": ["ls"]},
                                 agent_holds=POWERFUL)
    await sidecars.queue(mac, "terminal", "run", {"argv": ["rm", "-rf", "/"]},
                         agent_holds=POWERFUL)
    await sidecars.approve(first["id"], by="user:owner")

    jobs = await sidecars.next_jobs(mac)
    assert [j["id"] for j in jobs] == [first["id"]], (
        "approving one command released another the owner never saw")


@pytest.mark.asyncio
async def test_a_result_comes_back_only_from_the_machine_it_went_to(clean):
    mine, _ = await paired(["screenshot"], name="Mac")
    theirs, _ = await paired(["screenshot"], name="PC")
    job = await sidecars.queue(mine, "screenshot", "take", agent_holds=POWERFUL)
    await sidecars.next_jobs(mine)

    assert await sidecars.finish(theirs, job["id"], ok=True, result={}) is False
    assert await sidecars.finish(mine, job["id"], ok=True,
                                 result={"bytes": 12}) is True

    done = await sidecars.job(job["id"])
    assert done["status"] == "done" and done["result"]["bytes"] == 12


@pytest.mark.asyncio
async def test_a_failure_is_recorded_as_one(clean):
    mac, _ = await paired(["screenshot"])
    job = await sidecars.queue(mac, "screenshot", "take", agent_holds=POWERFUL)
    await sidecars.next_jobs(mac)
    await sidecars.finish(mac, job["id"], ok=False, error="no screen attached")

    done = await sidecars.job(job["id"])
    assert done["status"] == "failed" and "no screen" in done["error"]


@pytest.mark.asyncio
async def test_a_machine_that_was_asleep_has_jobs_waiting_not_running(clean):
    """A screenshot taken four hours late answers a question nobody is
    still asking."""
    mac, _ = await paired(["screenshot"])
    await sidecars.queue(mac, "screenshot", "take", agent_holds=POWERFUL)
    await clean.execute(
        "UPDATE sidecar_jobs SET expires_at = now() - interval '1 minute'")

    assert await sidecars.expire_old() == 1
    assert await sidecars.next_jobs(mac) == []


# --- the owner's controls --------------------------------------------------

@pytest.mark.asyncio
async def test_pausing_a_machine_stops_work_already_queued_for_it(clean):
    mac, _ = await paired(["screenshot"])
    job = await sidecars.queue(mac, "screenshot", "take", agent_holds=POWERFUL)
    await sidecars.set_status("Mac", "paused", by="user:owner")

    assert (await sidecars.job(job["id"]))["status"] == "cancelled"
    paused = await sidecars.authenticate(
        (await clean.fetchrow("SELECT token_hash FROM sidecars"))["token_hash"])
    assert paused is None, "the hash was accepted as a token"


@pytest.mark.asyncio
async def test_revoking_is_final(clean):
    _, token = await paired(["screenshot"])
    await sidecars.set_status("Mac", "revoked", by="user:owner")

    assert await sidecars.authenticate(token) is None
    # And it cannot be undone by whoever holds the laptop.
    assert await sidecars.set_status("Mac", "active", by="user:owner") is False
    assert await sidecars.authenticate(token) is None


@pytest.mark.asyncio
async def test_a_revoked_name_can_be_paired_again(clean):
    _, old = await paired(["screenshot"])
    await sidecars.set_status("Mac", "revoked", by="user:owner")

    _, new = await paired(["screenshot"])
    assert new != old
    assert await sidecars.authenticate(new) is not None
    assert await sidecars.authenticate(old) is None


@pytest.mark.asyncio
async def test_two_machines_cannot_share_a_name(clean):
    await paired(["screenshot"])
    with pytest.raises(SidecarError, match="already a sidecar"):
        await sidecars.offer_pairing("Mac", ["screenshot"], by="user:owner")


@pytest.mark.asyncio
async def test_every_job_is_written_down(clean):
    mac, _ = await paired(["screenshot"])
    await sidecars.queue(mac, "screenshot", "take", agent_holds=POWERFUL)

    rows = await clean.fetch(
        "SELECT * FROM audit_log WHERE action LIKE 'sidecar%'")
    assert len(rows) == 1
    assert rows[0]["actor"] == "sidecar:Mac"
    assert rows[0]["category"] != "low_risk", (
        "reaching another of the owner's machines was filed as low risk")


@pytest.mark.asyncio
async def test_with_nothing_paired_it_says_so(clean):
    state = await sidecars.state()
    assert state["sidecars"] == []
    assert "only reach its own server" in state["said"]


@pytest.mark.asyncio
async def test_two_copies_of_the_same_sidecar_do_not_both_take_a_job(clean):
    """Concurrently, not one after the other.

    Somebody will leave the sidecar running on a Mac and start it again
    over SSH without noticing. Sequential polling proves nothing about
    that -- the second call finds the row already marked sent. This runs
    both at once, on separate connections, which is the case the
    `FOR UPDATE SKIP LOCKED` in the claim is actually for.
    """
    import asyncio

    mac, _ = await paired(["screenshot"])
    for _ in range(5):
        await sidecars.queue(mac, "screenshot", "take", agent_holds=POWERFUL)

    grabbed = await asyncio.gather(*(sidecars.next_jobs(mac, 5) for _ in range(4)))

    ids = [str(j["id"]) for batch in grabbed for j in batch]
    assert len(ids) == len(set(ids)), (
        f"the same job went to more than one poller: {len(ids)} handed out, "
        f"{len(set(ids))} distinct")
    assert len(ids) == 5, f"expected all 5 handed out exactly once, got {len(ids)}"


@pytest.mark.asyncio
async def test_an_expired_job_is_not_handed_out_even_before_the_sweep(clean):
    """The sweep and the claim are two guards, and this is the second.

    The other expiry test calls `expire_old` first, so it proves the
    sweep marks things expired -- and would pass even if the claim
    itself happily handed out a job that ran out thirty seconds ago,
    before the sweep next ran.
    """
    mac, _ = await paired(["screenshot"])
    await sidecars.queue(mac, "screenshot", "take", agent_holds=POWERFUL)
    await clean.execute(
        "UPDATE sidecar_jobs SET expires_at = now() - interval '1 second'")

    assert await sidecars.next_jobs(mac) == [], (
        "a job past its expiry was handed out because the sweep had not run")


# --- the machine's own refusal --------------------------------------------
#
# The server decides what to ask for. The sidecar decides what it is
# willing to do. Both, independently -- so a server that has been
# tampered with does not thereby gain new hands. This is the half that
# lives on the owner's machine, tested here because it is the half
# nobody would think to test.

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402


def client():
    """The real sidecar script, loaded as a module."""
    path = Path(__file__).resolve().parents[2] / "sidecar" / "jarvis_sidecar.py"
    spec = importlib.util.spec_from_file_location("jarvis_sidecar", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_machine_refuses_what_it_was_not_paired_with():
    """Proven against the live pair too, by writing a 'terminal' job
    straight into the table past the server's own check: the machine
    still refused it."""
    sidecar = client()
    ok, _, why = sidecar.run_one(
        {"capability": "terminal", "arguments": {"argv": ["id"]}},
        granted={"screenshot", "notify"})
    assert ok is False
    assert "was not paired with 'terminal'" in why
    assert "whatever the server asked for" in why


def test_the_machine_does_what_it_was_paired_with():
    sidecar = client()
    ok, result, why = sidecar.run_one(
        {"capability": "files.read", "arguments": {"path": __file__}},
        granted={"files.read"})
    assert ok is True, why
    assert "the machine's own refusal" in result["text"]


def test_a_command_must_arrive_as_a_list_never_as_a_string():
    """There is no shell on the sidecar, on purpose.

    A command as one string is a shell command, and a shell command is
    where a semicolon means something -- which must not be true of a
    channel reaching the owner's laptop from somewhere else.
    """
    sidecar = client()
    for shaped_wrong in ("ls; rm -rf /", ["ls", 5], [], None, {"cmd": "ls"}):
        ok, _, why = sidecar.run_one(
            {"capability": "terminal", "arguments": {"argv": shaped_wrong}},
            granted={"terminal"})
        assert ok is False, f"accepted {shaped_wrong!r}"
        assert "list of strings" in why or "no shell" in why


def test_the_machine_only_opens_http_addresses():
    """A file:// or javascript: arriving from elsewhere is not a page to
    open, it is an instruction to this machine dressed as one."""
    sidecar = client()
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "/etc/passwd", ""):
        ok, _, why = sidecar.run_one(
            {"capability": "browser", "arguments": {"url": bad}},
            granted={"browser"})
        assert ok is False and "http and https" in why


def test_every_granted_capability_has_something_behind_it():
    """A capability the server can grant and the machine cannot perform
    is a job that always fails, discovered at the worst moment."""
    sidecar = client()
    assert set(sidecar.HANDLERS) == set(CAPABILITIES), (
        f"server grants {sorted(set(CAPABILITIES) - set(sidecar.HANDLERS))} "
        f"that the sidecar cannot do; sidecar does "
        f"{sorted(set(sidecar.HANDLERS) - set(CAPABILITIES))} that cannot be granted"
    )


def test_what_the_other_machines_may_do_is_not_jarvis_s_to_change():
    """If JARVIS could rewrite this it could grant itself a terminal on
    the owner's laptop, in a diff that would read like "support more
    capabilities"."""
    from app.constitution import is_protected

    for path in ("core/app/sidecars.py", "core/app/routes/sidecars.py"):
        why = is_protected(path)
        assert why, f"{path} can be rewritten by self-development"
        assert "your other machines" in why


# --- what kind of thing a sidecar is --------------------------------------
#
# An iPad cannot run a background program. iOS does not allow it, and no
# amount of wanting it to changes that. What it has is this dashboard,
# already open. So there are two kinds, and the kind decides what may be
# granted -- checked at PAIRING, because an iPad granted 'terminal' would
# take the grant happily and then fail every job for ever, for a reason
# knowable at the moment of granting.

from app.sidecars import KIND_CAN_DO, KINDS  # noqa: E402


@pytest.mark.asyncio
async def test_a_browser_cannot_be_granted_what_a_browser_cannot_do(clean):
    for impossible in ("terminal", "files.read", "files.write", "screenshot"):
        with pytest.raises(SidecarError, match="cannot do"):
            await sidecars.offer_pairing("iPad", ["notify", impossible],
                                         kind="browser", by="user:owner")


@pytest.mark.asyncio
async def test_the_refusal_says_what_that_kind_can_do(clean):
    with pytest.raises(SidecarError) as raised:
        await sidecars.offer_pairing("iPad", ["terminal"], kind="browser",
                                     by="user:owner")
    said = str(raised.value)
    for possible in sorted(KIND_CAN_DO["browser"]):
        assert possible in said, f"the refusal does not mention {possible}"


@pytest.mark.asyncio
async def test_a_browser_can_be_granted_what_it_can_do(clean):
    got = await sidecars.pair_this_browser(
        "iPad", ["speak", "notify", "browser"], by="user:owner")
    assert got["sidecar"]["kind"] == "browser"
    assert got["sidecar"]["capabilities"] == ["browser", "notify", "speak"]


@pytest.mark.asyncio
async def test_pairing_this_browser_needs_no_code(clean):
    """It is already signed in as him. Asking him to copy a code from a
    page into that same page is ceremony, and ceremony that achieves
    nothing is how people learn to click past security."""
    got = await sidecars.pair_this_browser("iPad", ["speak"], by="user:owner")
    assert await sidecars.authenticate(got["token"]) is not None
    assert await clean.fetchval("SELECT count(*) FROM sidecar_pairings") == 0


@pytest.mark.asyncio
async def test_pairing_the_same_device_again_replaces_it(clean):
    """He will open this dashboard on the same iPad many times. A list of
    forty 'iPad' entries is a list nobody reads."""
    first = await sidecars.pair_this_browser("iPad", ["speak"], by="user:owner")
    second = await sidecars.pair_this_browser("iPad", ["speak", "notify"],
                                              by="user:owner")

    assert await sidecars.authenticate(first["token"]) is None, (
        "the old pairing still works after re-pairing the same device")
    assert await sidecars.authenticate(second["token"]) is not None
    assert len([s for s in await sidecars.listing() if s["name"] == "iPad"]) == 1


@pytest.mark.asyncio
async def test_a_browser_row_cannot_run_a_native_capability_even_if_edited(clean):
    """The backstop. Pairing refuses it; this catches a row that predates
    the kinds, or one edited by hand."""
    got = await sidecars.pair_this_browser("iPad", ["speak"], by="user:owner")
    await clean.execute(
        "UPDATE sidecars SET capabilities = ARRAY['speak','terminal'] "
        "WHERE name = 'iPad'")
    ipad = await sidecars.authenticate(got["token"])

    assert "terminal" in ipad.capabilities, "the test did not set up what it claims"
    allowed, why = sidecars.may_run(ipad, "terminal", POWERFUL)
    assert allowed is False
    assert "cannot do 'terminal' at all" in why


def test_a_native_sidecar_can_do_everything_on_the_list():
    """If a capability existed that no kind could perform, it would be a
    grant that always fails."""
    covered = set().union(*KIND_CAN_DO.values())
    assert covered == set(CAPABILITIES), (
        f"no kind can perform: {sorted(set(CAPABILITIES) - covered)}")


def test_every_kind_is_explained_in_words():
    for kind, means in KINDS.items():
        assert means and not means.endswith("."), kind
        assert KIND_CAN_DO[kind], f"{kind} can do nothing"


def test_an_invented_kind_is_refused():
    with pytest.raises(SidecarError, match="A sidecar is"):
        sidecars._clean(["notify"], kind="toaster")
