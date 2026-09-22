"""What actually ends up inside the container.

Every test in this suite runs against the source tree. None of them had
ever looked at what the *image* contains -- so when `.dockerignore`
excluded `db/`, the Dockerfile's `COPY db/migrations` had no source, every
build failed, and 259 passing tests said nothing about it.

On Render a failed build is not an outage: the previous container keeps
serving. The site stays up and stays wrong, which is the worst shape a
failure can take -- there is nothing to notice.

These tests are cheap and touch no database. They read the Dockerfile and
the ignore files and check they agree with each other.
"""
import re
from fnmatch import fnmatch
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "infra" / "Dockerfile"


def _copy_sources() -> list[str]:
    """Every path the Dockerfile copies out of the build context."""
    sources = []
    for line in DOCKERFILE.read_text().splitlines():
        match = re.match(r"\s*COPY\s+(?!--from)(.+)", line)
        if not match:
            continue
        parts = [p for p in match.group(1).split() if not p.startswith("--")]
        # Flags are not paths. Without this, a perfectly good
        # `COPY --chown=root:root x ./x` reports that the repository is
        # missing a file called "--chown=root:root".
        sources.extend(parts[:-1])   # the last argument is the destination
    return sources


def _patterns(name: str) -> list[str]:
    text = (ROOT / name).read_text().splitlines()
    return [line.strip() for line in text
            if line.strip() and not line.strip().startswith("#")]


def _excluded(path: str, patterns: list[str]) -> bool:
    """Would the build context drop this path?

    Docker's rule is last match wins, with `!` re-including. A path is
    also gone if any directory above it was excluded and not re-included.
    """
    verdict = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        bare = pattern.lstrip("!").rstrip("/")
        parts = Path(path).parts
        ancestors = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        if any(fnmatch(a, bare) for a in ancestors):
            verdict = not negate
    return verdict


@pytest.mark.parametrize("ignore_file", [".dockerignore", ".gcloudignore"])
def test_everything_the_dockerfile_copies_survives_the_build_context(ignore_file):
    """The bug this file exists for.

    An excluded COPY source fails the build, and a failed build on Render
    leaves the old container running. Nothing goes red; the deployment
    simply stops changing.
    """
    patterns = _patterns(ignore_file)
    for source in _copy_sources():
        assert not _excluded(source, patterns), (
            f"{ignore_file} excludes '{source}', which infra/Dockerfile "
            f"copies -- the build will fail with no source for it"
        )


def test_everything_the_dockerfile_copies_actually_exists():
    for source in _copy_sources():
        assert (ROOT / source).exists(), (
            f"infra/Dockerfile copies '{source}', which is not in the repo"
        )


def test_the_container_gets_the_migrations_it_is_expected_to_apply():
    """The specific promise: JARVIS applies its own schema at startup.

    It can only do that if the files are in the image. This asserts the
    whole chain -- the migrations exist, the Dockerfile copies them, and
    nothing filters them back out -- rather than any one link of it.
    """
    migrations = sorted((ROOT / "db" / "migrations").glob("*.sql"))
    assert migrations, "there are no migrations to ship"

    assert any(s.rstrip("/") == "db/migrations" for s in _copy_sources()), (
        "the Dockerfile does not copy db/migrations, so the container "
        "cannot apply its own schema"
    )
    for ignore_file in (".dockerignore", ".gcloudignore"):
        patterns = _patterns(ignore_file)
        for migration in migrations:
            relative = migration.relative_to(ROOT).as_posix()
            assert not _excluded(relative, patterns), (
                f"{ignore_file} would drop {relative} from the build"
            )


def test_the_migrations_land_where_the_app_looks_for_them():
    """WORKDIR plus COPY destination has to equal what _migrations_dir() checks.

    Two files agreeing by coincidence is how this breaks: the app looks
    beside its own package, and the Dockerfile decides where that is.
    """
    text = DOCKERFILE.read_text()
    assert re.search(r"^WORKDIR /app\s*$", text, re.M), (
        "WORKDIR moved; app/migrate.py resolves the migrations relative to it"
    )
    # Flags tolerated, destination not: `--chown=root:root` is fine,
    # a different destination moves the code out from under the app.
    assert re.search(r"^COPY (?:--\S+ )*core/app \./app\s*$", text, re.M)
    assert re.search(r"^COPY (?:--\S+ )*db/migrations \./db/migrations\s*$",
                     text, re.M), (
        "the migrations must land at /app/db/migrations, which is where "
        "app/migrate.py._migrations_dir() looks"
    )


def test_the_constitution_reaches_the_container():
    """A protection that reports itself missing in production and nowhere
    else is the worst place for one to be absent.

    The paths are enforced in code regardless of this file, so its absence
    would not open the protected core -- but /health would report
    `present: false` and the fingerprint would be gone, which is the half
    that lets an unexpected change be noticed.
    """
    assert any("CONSTITUTION.md" in source for source in _copy_sources()), (
        "infra/Dockerfile does not copy CONSTITUTION.md into the image"
    )


def test_the_constitution_is_found_from_where_the_app_actually_runs():
    """A checkout has it three levels above app/constitution.py; the
    container has it two. Both are looked in, because getting this wrong
    fails only in production."""
    import sys

    sys.path.insert(0, str(ROOT / "core"))
    from app import constitution

    assert constitution.state()["present"] is True
    assert constitution.digest()


# --- who the container runs as ---------------------------------------------
#
# Brief section 32: "Do not automatically give the main autonomous JARVIS
# runtime unrestricted Docker-host authority." The image had no USER
# directive at all, so it ran as root -- caught by reading the Dockerfile
# while answering "is the prompt implemented", not by any test here.
#
# Root in a container is not root on the host. It is one container escape
# away from it, which is a different statement.

COMPOSE = ROOT / "docker-compose.yml"


def _directives() -> list[tuple[str, str]]:
    """Every Dockerfile instruction in order, as (INSTRUCTION, rest)."""
    found = []
    for line in DOCKERFILE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head, _, rest = stripped.partition(" ")
        found.append((head.upper(), rest.strip()))
    return found


def test_the_container_does_not_run_as_root():
    users = [rest for head, rest in _directives() if head == "USER"]
    assert users, (
        "infra/Dockerfile has no USER directive, so the application runs "
        "as root inside the container"
    )
    assert users[-1].split(":")[0] not in ("root", "0"), (
        f"the container ends up running as {users[-1]}"
    )


def test_the_user_it_switches_to_is_one_the_image_actually_creates():
    """`USER jarvis` without a matching useradd is not a privilege drop,
    it is a container that will not start."""
    order = _directives()
    name = [rest for head, rest in order if head == "USER"][-1].split(":")[0]
    created = " ".join(rest for head, rest in order if head == "RUN")
    assert name in created, (
        f"the Dockerfile switches to '{name}', which it never creates"
    )


def test_the_privileges_are_dropped_after_the_install_and_never_regained():
    """Ordering is the whole correctness of this.

    USER before `pip install` fails the build; a later `USER root` undoes
    the drop and would look perfectly reasonable in a diff.
    """
    order = _directives()
    first_user = next(i for i, (head, _) in enumerate(order) if head == "USER")

    installs = [i for i, (head, rest) in enumerate(order)
                if head == "RUN" and "pip install" in rest]
    assert installs, "no pip install step found; this test is out of date"
    assert max(installs) < first_user, (
        "the Dockerfile drops privileges before installing dependencies, "
        "which fails the build"
    )

    after = [rest for head, rest in order[first_user + 1:] if head == "USER"]
    assert not any(u.split(":")[0] in ("root", "0") for u in after), (
        "the Dockerfile returns to root after dropping privileges"
    )


def test_jarvis_cannot_write_the_code_it_is_running():
    """The second enforcement of the protected core, by the filesystem.

    /app is copied as root and left that way, so the runtime user can read
    its own source and not modify it. A `chown` to the runtime user would
    silently undo that -- and would be an easy thing to add while fixing
    some unrelated permission error.
    """
    for head, rest in _directives():
        if head in ("RUN", "COPY", "ADD") and "chown" in rest:
            assert "root" in rest, (
                f"'{head} {rest}' hands the application code to a non-root "
                f"owner, letting the running JARVIS rewrite its own source"
            )


def test_the_compose_file_does_not_hand_over_the_docker_socket():
    """Brief section 32, named explicitly: "unrestricted Docker socket
    control can effectively become host-level authority"."""
    text = COMPOSE.read_text()
    for danger in ("docker.sock", "privileged: true", "cap_add",
                   "network_mode: host", "pid: host"):
        assert danger not in text, (
            f"docker-compose.yml contains '{danger}', which gives the "
            f"container authority over the machine running it"
        )


def test_the_compose_file_does_not_put_root_back():
    """The image drops to a normal user; `user: root` in compose would
    override it without touching the Dockerfile at all."""
    text = COMPOSE.read_text()
    assert not re.search(r"^\s*user:\s*[\"']?(root|0)\b", text, re.M), (
        "docker-compose.yml overrides the image's user back to root"
    )


# --- can self-development work in the container at all? --------------------
#
# It could not, and nothing said so. The image is a COPY of the source
# with no .git in it (.dockerignore excludes it), so inside the container
# repo.state() reports "not a git repository", the Plan button stays
# disabled, and the Build tab renders perfectly while doing nothing.
#
# That is how it reached Sagar's own server: a page with every tab in
# place and no working back end.


def _compose() -> str:
    return COMPOSE.read_text()


def test_the_repository_is_mounted_so_jarvis_can_write_to_itself():
    """The image has no history of its own; it must be given one."""
    assert re.search(r"^\s*-\s*\./:/repo\s*$", _compose(), re.M), (
        "docker-compose.yml does not mount the repository into the "
        "container, so JARVIS cannot build changes to itself at all"
    )


def test_the_mount_is_writable():
    """A read-only mount makes the Build tab fail later and less clearly:
    it would plan, open a worktree, and die on the first write."""
    assert not re.search(r"^\s*-\s*\./:/repo:ro\s*$", _compose(), re.M), (
        "the repository is mounted read-only, so every build will fail "
        "part way through instead of not starting"
    )


def test_the_container_is_told_where_the_repository_is():
    """Mounting it is not enough. Without REPO_PATH, JARVIS looks beside
    its own code -- which inside the image is '/', not a repository."""
    assert re.search(r'^\s*REPO_PATH:\s*"?/repo"?\s*$', _compose(), re.M), (
        "docker-compose.yml mounts the repository but never tells JARVIS "
        "where it is, so it will still report that it cannot build"
    )


def test_it_runs_as_a_user_that_can_write_the_mounted_repository():
    """The image drops to uid 10001, which does not own the host
    checkout. Every build would fail inside git, with a message about the
    object store rather than about ownership."""
    match = re.search(r'^\s*user:\s*"?([^"\n]+)"?\s*$', _compose(), re.M)
    assert match, (
        "the compose file does not override the image's user, so the "
        "container runs as uid 10001 and cannot write to your checkout"
    )
    assert "10001" not in match.group(1)


def test_the_constitution_is_still_mounted_read_only_over_the_repository():
    """Ordering matters: /repo is read-write and contains its own copy of
    CONSTITUTION.md. The read-only mount at /app/CONSTITUTION.md is what
    the running code reads, and it must survive."""
    text = _compose()
    assert "./CONSTITUTION.md:/app/CONSTITUTION.md:ro" in text


# --- the browser, which is optional and must stay optional ----------------

def test_the_browser_is_off_unless_the_build_asks_for_it():
    """~400MB and several minutes. Most deployments never need it.

    The point of the default is that somebody who has never heard of any
    of this gets a small image that works.
    """
    text = DOCKERFILE.read_text()
    assert re.search(r"^ARG WITH_BROWSER=false", text, re.M), (
        "the browser is not behind a build argument defaulting to off"
    )


def test_the_browser_is_installed_after_pip_and_before_the_user_drops():
    """Two orderings, both of which would fail the build at 400MB in.

    `playwright install` is a command the pip package provides, so it
    cannot run before pip. And it writes into /usr/lib and /ms-playwright
    as root, so it cannot run after the image drops to the jarvis user --
    which is the whole point of dropping to it.
    """
    lines = DOCKERFILE.read_text().splitlines()

    def line_of(pattern):
        for n, line in enumerate(lines):
            if re.search(pattern, line):
                return n
        raise AssertionError(f"nothing in the Dockerfile matches {pattern!r}")

    pip = line_of(r"^RUN pip install")
    browser = line_of(r"playwright install")
    user = line_of(r"^USER ")

    assert pip < browser, "the browser is installed before pip provides it"
    assert browser < user, (
        "the browser is installed after privileges are dropped, so it "
        "cannot write where it needs to"
    )


def test_the_browser_install_is_guarded_by_the_argument():
    """Not merely present: actually conditional.

    An unguarded `playwright install` would add 400MB to every image
    built by anyone, for a capability most of them will never use.
    """
    text = DOCKERFILE.read_text()
    install = next(l for l in text.splitlines() if "playwright install" in l)
    assert 'if [ "$WITH_BROWSER" = "true" ]' in install, (
        f"the browser install is not conditional: {install!r}"
    )


def test_playwright_is_a_declared_dependency():
    """The pip package, which is small, is not optional -- only the
    browser binary is. Without the package the fallback cannot even
    report that it has no browser."""
    requirements = (ROOT / "core" / "requirements.txt").read_text()
    assert re.search(r"^playwright[><=]", requirements, re.M)


# --- the script that checks all of this on a machine with Docker ----------

def test_there_is_a_way_to_verify_the_container_where_docker_exists():
    """Two items in the brief have been unticked for months for one
    honest reason: no Docker daemon here. This is what closes them."""
    script = ROOT / "scripts" / "verify_container.sh"
    assert script.is_file(), "there is no container verification script"
    assert script.stat().st_mode & 0o111, f"{script.name} is not executable"

    text = script.read_text()
    # The two things it exists to prove, beyond "it builds".
    assert "--read-only" in text, (
        "the script does not test the read-only filesystem, which is one "
        "of the two items it is meant to close"
    )
    # `--entrypoint id`, because the argument follows the image name.
    assert "--entrypoint id" in text and '"$IMAGE" -u' in text, (
        "the script does not check what user the container runs as"
    )
    # The probe itself, not merely the path -- which also appears on the
    # line that tidies the probe up, so checking for the path alone
    # passed with the probe replaced by `true`.
    assert "touch /app/app/__probe" in text, (
        "the script does not try to WRITE into /app, which is the "
        "Constitution's boundary enforced by the filesystem"
    )


def test_the_verification_script_cleans_up_after_itself():
    """It starts a database and a container on somebody's real machine."""
    text = (ROOT / "scripts" / "verify_container.sh").read_text()
    assert "trap cleanup EXIT" in text, "it can leave containers running"
    assert "docker rm -f" in text and "docker network rm" in text


def test_the_verification_script_never_touches_a_real_jarvis():
    """Its own names, its own port, its own database. Somebody will run
    this on the machine their JARVIS is running on."""
    text = (ROOT / "scripts" / "verify_container.sh").read_text()
    for own in ("jarvis-verify-db", "jarvis-verify-app", "jarvis-verify-net"):
        assert own in text, f"the script does not use its own {own}"
    assert "8791" in text, "it does not use a port of its own"
    assert "POSTGRES_DB=jarvis" in text and "postgres:16" in text
