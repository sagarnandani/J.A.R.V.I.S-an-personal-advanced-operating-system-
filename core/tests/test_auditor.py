"""The reviewer, and the things it must not miss.

The mechanical half is what these test. It is the half that cannot be
argued with, and every check in it exists because the alternative is
trusting the model that wrote the change to report on the change it
wrote.
"""
import pytest

from app.dev import auditor


def a_diff(path: str, added: list[str], removed: list[str] = ()) -> str:
    body = "".join(f"+{line}\n" for line in added)
    body += "".join(f"-{line}\n" for line in removed)
    return (f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,1 @@\n{body}")


def test_it_reads_the_paths_out_of_the_diff_not_the_claim():
    """The whole question an auditor answers is whether what happened
    matches what was claimed, so it cannot take the claim as input."""
    report = auditor.inspect(a_diff("core/app/thing.py", ["VALUE = 2"]))
    assert report["files"] == ["core/app/thing.py"]


def test_a_protected_file_in_the_diff_is_blocking():
    """This should be unreachable -- the write boundary refuses it. That
    is exactly why it is checked again: a protection nobody verifies
    independently is one nobody would notice losing."""
    report = auditor.inspect(
        a_diff("core/app/agents/permissions.py", ["ALLOWED = True"]))
    assert any("protected core" in b for b in report["blocking"])
    assert any("write boundary has a hole" in b for b in report["blocking"])


def test_a_file_the_plan_never_mentioned_is_blocking():
    plan = {"files": [{"path": "core/app/a.py"}]}
    report = auditor.inspect(a_diff("core/app/b.py", ["X = 1"]), plan)
    assert any("never mentioned" in b for b in report["blocking"])


def test_a_file_the_plan_did_mention_is_not():
    plan = {"files": [{"path": "core/app/a.py"}]}
    report = auditor.inspect(a_diff("core/app/a.py", ["X = 1"]), plan)
    assert not any("never mentioned" in b for b in report["blocking"])


@pytest.mark.parametrize("line,expected", [
    ("    permissions = frozenset({Permission.PUBLISH})", "may do"),
    ("NEVER_DELEGATED = set()", "never-delegated"),
    ("ALWAYS_APPROVED = {}", "needs approval"),
    ("    DEV_MODE = True", "authentication bypass"),
    ("    COOKIE_SECURE = False", "cookie security"),
])
def test_widening_authority_is_blocking(line, expected):
    report = auditor.inspect(a_diff("core/app/thing.py", [line]))
    assert any(expected in b for b in report["blocking"]), report["blocking"]


@pytest.mark.parametrize("line,expected", [
    ("    os.system('rm -rf /')", "shell command"),
    ("    subprocess.run(['curl', url])", "subprocess"),
    ("    eval(user_input)", "evaluates code"),
    ("    exec(payload)", "executes code"),
    ("    requests.get(url, verify=False)", "certificate verification"),
    ("    KEY = 'sk-abcdefghijklmnop1234'", "hard-coded API key"),
    ("    run('git push origin main')", "move code out of its branch"),
])
def test_dangerous_additions_are_blocking(line, expected):
    report = auditor.inspect(a_diff("core/app/thing.py", [line]))
    assert any(expected in b for b in report["blocking"]), report["blocking"]


@pytest.mark.parametrize("line,expected", [
    ("        raise PermissionDenied('no')", "removes a permission refusal"),
    ("    await refuse_if_stopped()", "removes an emergency-stop check"),
])
def test_removing_a_safety_check_is_blocking(line, expected):
    report = auditor.inspect(a_diff("core/app/thing.py", [], [line]))
    assert any(expected in b for b in report["blocking"]), report["blocking"]


def test_a_new_dependency_is_blocking():
    report = auditor.inspect(a_diff("core/requirements.txt", ["leftpad==1.0"]))
    assert any("dependency" in b for b in report["blocking"])


def test_a_rewrite_that_deletes_far_more_than_it_writes_is_blocking():
    """Either a deliberate deletion or a model that lost the file. Both
    want a person; neither wants an unattended approval."""
    report = auditor.inspect(
        a_diff("core/app/thing.py", ["X = 1"],
               [f"LINE_{i} = {i}" for i in range(50)]))
    assert any("removes 50 lines" in b for b in report["blocking"])


def test_an_ordinary_change_is_not_blocked():
    """The check that stops this from being a test suite that only proves
    the auditor says no to everything."""
    report = auditor.inspect(
        a_diff("core/app/media/script.py",
               ["def summarise(text):", "    return text[:200]"]),
        {"files": [{"path": "core/app/media/script.py"}]},
        tests_passed=True)
    assert report["blocking"] == []
    assert report["added"] == 2


def test_an_empty_diff_is_blocking():
    assert any("empty" in b for b in auditor.inspect("")["blocking"])


def test_it_says_what_it_checked():
    report = auditor.inspect(a_diff("a.py", ["X = 1"]))
    assert report["checked"] == "mechanical"


def test_rollback_is_stated_rather_than_assumed():
    report = auditor.inspect(a_diff("a.py", ["X = 1"]))
    assert any("never merged" in n for n in report["noted"])


# --- the model half ---------------------------------------------------------

@pytest.mark.asyncio
async def test_a_missing_second_opinion_is_never_a_pass(monkeypatch):
    """"The reviewing model was down" and "the reviewing model approved"
    must never reach the Governor looking the same."""
    import app.llm

    def broken(settings, want=None):
        raise RuntimeError("no provider")

    monkeypatch.setattr(app.llm, "get_provider", broken)
    report = await auditor.audit(
        brief="b", plan={"files": [{"path": "a.py"}]},
        diff=a_diff("a.py", ["X = 1"]), tests_passed=True, settings=None)

    assert report["second_opinion"]["available"] is False
    assert any("no second opinion" in n for n in report["noted"])
    assert report["checked"] == "mechanical only"


@pytest.mark.asyncio
async def test_a_reviewer_saying_no_is_blocking(monkeypatch):
    import json
    from types import SimpleNamespace

    import app.llm

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            return SimpleNamespace(
                text=json.dumps({"does_what_was_asked": False,
                                 "why": "it does something else entirely",
                                 "concerns": ["a concern"],
                                 "beyond_scope": ["an extra thing"]}),
                input_tokens=1, output_tokens=1, model="m", provider="mock")

    monkeypatch.setattr(app.llm, "get_provider", lambda s, want=None: Fake())
    report = await auditor.audit(
        brief="b", plan={"files": [{"path": "a.py"}]},
        diff=a_diff("a.py", ["X = 1"]), tests_passed=True, settings=None)

    assert any("does not do what was asked" in b for b in report["blocking"])
    assert any("beyond what was asked" in b for b in report["blocking"])
    assert any("a concern" in n for n in report["noted"])


@pytest.mark.asyncio
async def test_a_review_by_the_only_provider_is_not_called_independent(
        monkeypatch):
    """Reviewing your own work is not review, and the report must say so
    rather than letting 'audited' imply a second opinion that never
    happened."""
    import json
    from types import SimpleNamespace

    import app.llm

    class Fake:
        async def complete(self, message, history=None, memory_context=None):
            return SimpleNamespace(
                text=json.dumps({"does_what_was_asked": True, "why": "fine",
                                 "concerns": [], "beyond_scope": []}),
                input_tokens=1, output_tokens=1, model="m", provider="mock")

    def only_one(settings, want=None):
        if want is not None:
            raise RuntimeError("not configured")
        return Fake()

    monkeypatch.setattr(app.llm, "get_provider", only_one)
    report = await auditor.audit(
        brief="b", plan={"files": [{"path": "a.py"}]},
        diff=a_diff("a.py", ["X = 1"]), tests_passed=True, settings=None,
        wrote_with="gemini")

    assert report["second_opinion"]["independent"] is False
    assert any("not an independent review" in n for n in report["noted"])


@pytest.mark.asyncio
async def test_the_author_is_never_asked_to_review_itself(monkeypatch):
    import json
    from types import SimpleNamespace

    import app.llm

    asked = []

    class Fake:
        def __init__(self, name):
            self.name = name

        async def complete(self, message, history=None, memory_context=None):
            asked.append(self.name)
            return SimpleNamespace(
                text=json.dumps({"does_what_was_asked": True, "why": "fine",
                                 "concerns": [], "beyond_scope": []}),
                input_tokens=1, output_tokens=1, model="m", provider=self.name)

    monkeypatch.setattr(app.llm, "get_provider",
                        lambda s, want=None: Fake(want or "default"))
    await auditor.audit(brief="b", plan={"files": [{"path": "a.py"}]},
                        diff=a_diff("a.py", ["X = 1"]), tests_passed=True,
                        settings=None, wrote_with="claude")

    assert asked and "claude" not in asked
