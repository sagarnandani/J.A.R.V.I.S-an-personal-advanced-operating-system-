# JARVIS Core -- Stage 0
#
# Portability requirement (architecture doc, section D): this container
# must be redeployable to any container platform -- Cloud Run today, a Mac
# mini or home server later -- with no rewrite, just `docker build` +
# `docker run` pointed at a Postgres URL.
FROM python:3.11-slim

WORKDIR /app

COPY core/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/app ./app
COPY core/static ./static
# The Constitution. Without it in the image, the protected core reports
# itself missing in production and nowhere else -- the worst place for a
# protection to be absent. The paths it protects are enforced in code
# regardless; this is the document those rules are written in, and what
# /health fingerprints so an unexpected change is visible.
COPY CONSTITUTION.md ./CONSTITUTION.md
# The schema, so the container can bring the database up to date itself.
# Without this JARVIS cannot see its own migrations and every schema
# change needs a human with a SQL editor.
COPY db/migrations ./db/migrations

# JARVIS runs as a normal user, never as root.
#
# Section 32 of the self-development brief: "Do not automatically give
# the main autonomous JARVIS runtime unrestricted Docker-host authority."
# Root inside a container is not root on the host, but it is one
# container-escape away from it, and there is no reason to be holding it
# -- nothing here writes to disk, binds a low port, or installs anything
# after the build.
#
# Note what is deliberately NOT done: /app is left owned by root. The
# jarvis user can read its own code and cannot modify it. So even if
# something managed to run code inside this container, it could not
# rewrite the application it is running -- which is the same boundary
# the Constitution draws in app/constitution.py, enforced a second time
# by the filesystem rather than by JARVIS's own good behaviour.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin jarvis
USER jarvis

ENV PYTHONUNBUFFERED=1
# /app is not writable by this user, so Python could not cache bytecode
# there anyway. Saying so outright avoids a failed write on every import.
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080

# Cloud Run sets $PORT; default to 8080 for local/other hosts.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
