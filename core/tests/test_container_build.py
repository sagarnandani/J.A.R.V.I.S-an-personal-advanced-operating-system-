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
        parts = match.group(1).split()
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
    assert re.search(r"^COPY core/app \./app\s*$", text, re.M)
    assert re.search(r"^COPY db/migrations \./db/migrations\s*$", text, re.M), (
        "the migrations must land at /app/db/migrations, which is where "
        "app/migrate.py._migrations_dir() looks"
    )
