"""Git, with the dangerous parts removed rather than guarded.

JARVIS runs on the owner's own machine now, and the version of
self-modification that ends badly is a process editing the files it is
currently running from. So it does not.

Every change happens in a **separate worktree** on a **new branch**. A
worktree is a second checkout of the same repository in another
directory, sharing history but with its own files, so writing in it
cannot touch the running deployment even by accident. The running
checkout's branch is never switched, never reset, never stashed.

Three things this module will not do, and does not have code for:

* **Push.** Nothing here talks to a remote. The owner pushes, if he wants
  to, from a branch he has read.
* **Merge.** Nothing here merges, fast-forwards or rebases anything onto
  anything.
* **Write outside the worktree.** Every path is resolved and checked to
  be inside it before a byte is written, so a plan naming `../../etc` is
  refused rather than followed.

What is left is: make a branch, write files in an isolated copy, run the
repository's own tests there, and produce a diff. All of which the owner
reads before any of it is real.
"""
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

from app import constitution

logger = logging.getLogger("jarvis.dev.repo")

# How long any one git or test command may take. A hung test suite should
# be a reported failure, not a worktree that never finishes.
GIT_TIMEOUT = 60
TEST_TIMEOUT = 900
# pytest's exit code for "I found no tests to run".
NOTHING_COLLECTED = 5

# Branches a change may never be built on, whatever a brief says.
PROTECTED = {"main", "master", "trunk", "production", "release"}

# Where worktrees live. Beside the repository rather than inside it, so a
# half-finished one can never be picked up as project files.
WORKTREE_DIR = ".jarvis-worktrees"


class RepoError(Exception):
    """Said plainly. Every refusal here is something the owner needs to
    read, not a stack trace in a log nobody opens."""


async def _git(*args: str, cwd: Path, timeout: int = GIT_TIMEOUT) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        # A git command that stops to ask something would hang for ever
        # with nobody at the terminal to answer it.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise RepoError(f"git {args[0]} took longer than {timeout}s.") from exc

    text = (out or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RepoError(f"git {' '.join(args)} failed:\n{text.strip()[:2000]}")
    return text


def root(settings=None) -> Path:
    """The repository JARVIS is running from.

    Configured rather than guessed, because "the directory above the code"
    is true today and would stop being true the first time this is
    packaged differently.
    """
    configured = getattr(settings, "repo_path", "") if settings else ""
    here = Path(configured) if configured else Path(__file__).resolve().parents[3]
    return here


async def state(settings=None) -> dict:
    """Whether this deployment can build changes at all, and why not.

    Asked before anything is offered. A brief that becomes a plan and then
    discovers there is no git repository has wasted a model call and the
    owner's attention.
    """
    where = root(settings)
    info = {"path": str(where), "usable": False, "why": "", "branch": None,
            "clean": None, "head": None}

    if shutil.which("git") is None:
        info["why"] = "git is not installed on this machine."
        return info
    if not (where / ".git").exists():
        info["why"] = f"{where} is not a git repository."
        return info

    try:
        info["branch"] = (await _git("rev-parse", "--abbrev-ref", "HEAD",
                                     cwd=where)).strip()
        # The exact commit, so a change can record what it was written
        # against and recovery has something specific to name. Reading a
        # revision is not moving to one -- nothing here can check out.
        info["head"] = (await _git("rev-parse", "HEAD", cwd=where)).strip()
        dirty = (await _git("status", "--porcelain", cwd=where)).strip()
    except RepoError as exc:
        info["why"] = str(exc)
        return info

    info["clean"] = not dirty
    if dirty:
        # Building from a dirty checkout would put the owner's own
        # unfinished edits into a branch he did not write, and the diff
        # would blame JARVIS for them.
        info["why"] = (
            "The working copy has uncommitted changes. Commit or stash "
            "them first, so a proposed change contains only what JARVIS "
            "wrote."
        )
        return info

    info["usable"] = True
    return info


def branch_name(title: str) -> str:
    """A branch named after the change, readable in a list of branches."""
    slug = "".join(c if c.isalnum() else "-" for c in (title or "change").lower())
    slug = "-".join(part for part in slug.split("-") if part)[:48].strip("-")
    return f"jarvis/{slug or 'change'}"


async def open_worktree(title: str, settings=None) -> dict:
    """A branch and an isolated checkout to build it in.

    Branched from the current HEAD rather than from a remote: this is the
    code actually running, which is what a change to it should be based
    on.
    """
    where = root(settings)
    ready = await state(settings)
    if not ready["usable"]:
        raise RepoError(ready["why"] or "This deployment cannot build changes.")

    branch = branch_name(title)
    if branch.rsplit("/", 1)[-1] in PROTECTED or branch in PROTECTED:
        raise RepoError(f"Refusing to build on {branch}.")

    holder = where.parent / WORKTREE_DIR
    holder.mkdir(parents=True, exist_ok=True)
    path = holder / branch.replace("/", "-")
    if path.exists():
        # A leftover from a previous attempt. Removed through git so the
        # repository's own bookkeeping stays consistent.
        await close_worktree(str(path), settings)

    await _git("worktree", "add", "-b", branch, str(path), "HEAD", cwd=where)
    return {"branch": branch, "path": str(path), "base": ready["branch"]}


async def close_worktree(path: str, settings=None) -> None:
    """Take the checkout away and leave the branch.

    The branch is the deliverable, and removing it because the temporary
    directory was cleaned up would throw away the thing the owner was
    meant to read.
    """
    where = root(settings)
    try:
        await _git("worktree", "remove", "--force", path, cwd=where)
    except RepoError:
        shutil.rmtree(path, ignore_errors=True)
        try:
            await _git("worktree", "prune", cwd=where)
        except RepoError:
            pass


def inside(worktree: str, relative: str, *, writing: bool = True) -> Path:
    """Resolve a path, or refuse it.

    Two refusals, and the second is the protected core.

    The first stops a plan naming `../../.ssh/authorized_keys` from being
    followed. Resolved first, because `a/../../b` is only obviously
    outside once it has been.

    The second stops JARVIS writing the rules that govern JARVIS. It is
    here, at the single point every write passes through, rather than in
    the planner or the prompt, because this is the boundary that cannot
    be reasoned around: a model can be persuaded, a prompt can be
    diluted, and a plan can be wrong. A function that refuses cannot be
    talked out of it.

    Reading is allowed. JARVIS should be able to read its own
    Constitution, reason with it, and say when a brief conflicts with it.
    What it may not do is write it.
    """
    base = Path(worktree).resolve()
    target = (base / relative).resolve()
    try:
        inside_tree = target.relative_to(base)
    except ValueError as exc:
        raise RepoError(f"Refusing to write outside the worktree: {relative}") from exc
    if ".git" in target.parts:
        raise RepoError(f"Refusing to write into git's own files: {relative}")

    if writing:
        why = constitution.is_protected(str(inside_tree))
        if why:
            raise RepoError(
                f"Refusing to change {inside_tree}: {why} is part of the "
                f"protected core. JARVIS may read it and say what it thinks "
                f"about it, and changing it is yours to do by hand."
            )
    return target


async def read_file(worktree: str, relative: str, limit: int = 60_000) -> str:
    # Reading is not restricted. JARVIS should be able to read its own
    # Constitution and reason about it; the protection is on writing.
    path = inside(worktree, relative, writing=False)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


async def write_file(worktree: str, relative: str, content: str) -> None:
    path = inside(worktree, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def run_tests(worktree: str, command: list[str] | None = None) -> dict:
    """The repository's own tests, inside the worktree.

    A proposal with no test result is not a proposal. Failing tests do not
    stop the change from being shown -- the owner may well want to see
    what it tried -- but they are reported as failing, plainly, and the
    change is never described as working.
    """
    # sys.executable, never a bare "python". The interpreter on PATH is
    # frequently not the one JARVIS is running under -- a virtualenv, a
    # container with a system python beside the app's -- and when it is
    # not, pytest is missing from it and EVERY change comes back as "the
    # tests fail". A test signal that is always red is worse than none,
    # because it is the one people learn to ignore.
    command = command or [sys.executable, "-m", "pytest", "-q"]
    cwd = Path(worktree) / "core"
    if not cwd.is_dir():
        cwd = Path(worktree)

    try:
        # Verifying a change must not become part of it. Without this,
        # the test run drops __pycache__/*.pyc into the worktree, the
        # commit picks them up, and the auditor -- correctly -- reports
        # files the plan never mentioned, on every single change.
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=TEST_TIMEOUT)
        text = (out or b"").decode("utf-8", errors="replace")

        # pytest exits 5 when it collected nothing. That is not a failure
        # and it is not a pass: it is "nobody checked", which is what
        # `None` means here and what the Governor refuses to approve on.
        # Reporting it as a failure -- which this did -- would mark a
        # perfectly good change as broken, and reporting it as a pass
        # would approve unverified code. Both are worse than saying so.
        # The interpreter has no pytest. Not a failing test suite: no
        # test suite was run at all.
        if "No module named pytest" in text:
            return {"passed": None, "command": " ".join(command),
                    "output": "pytest is not installed for "
                              f"{sys.executable}, so nothing about this "
                              "change was verified.\n\n" + text[-4000:]}

        if proc.returncode == NOTHING_COLLECTED:
            return {"passed": None, "command": " ".join(command),
                    "output": "No tests were collected, so nothing about "
                              "this change was verified.\n\n" + text[-4000:]}

        return {"passed": proc.returncode == 0, "output": text[-6000:],
                "command": " ".join(command)}
    except asyncio.TimeoutError:
        proc.kill()
        return {"passed": False, "command": " ".join(command),
                "output": f"The tests did not finish within {TEST_TIMEOUT}s."}
    except FileNotFoundError as exc:
        return {"passed": None, "command": " ".join(command),
                "output": f"Could not run the tests: {exc}"}


async def commit(worktree: str, message: str, settings=None) -> dict:
    """Commit what was written, and hand back the diff.

    Committed inside the worktree, which is on its own branch, so the
    running checkout's HEAD does not move.
    """
    path = Path(worktree)
    await _git("add", "-A", cwd=path)

    staged = (await _git("diff", "--cached", "--name-only", cwd=path)).strip()
    if not staged:
        return {"committed": False, "files": [], "diff": "",
                "why": "Nothing was changed."}

    # The last boundary. Everything should have been refused already at
    # the write, so reaching here means something arrived by a route this
    # module does not know about -- which is exactly when a final check
    # earns its keep. The commit is abandoned rather than partially made.
    forbidden = constitution.refuse(staged.splitlines())
    if forbidden:
        # Raised rather than unstaged. Nothing is committed, and the
        # whole worktree is discarded by the caller a moment later, so
        # cleaning up here would only add a git subcommand this module is
        # better off not having at all.
        raise RepoError(
            "Refusing to commit changes to the protected core: "
            + "; ".join(forbidden)
        )

    await _git("-c", "user.name=JARVIS",
               "-c", "user.email=jarvis@localhost",
               "commit", "-m", message[:2000], cwd=path)

    diff = await _git("show", "--stat", "--patch", "HEAD", cwd=path)
    return {
        "committed": True,
        "files": [f for f in staged.splitlines() if f],
        # Bounded: a diff nobody can scroll through is a diff nobody
        # reads, and a proposal that cannot be read cannot be approved.
        "diff": diff[:120_000],
        "why": "",
    }
