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

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

# Cloud Run sets $PORT; default to 8080 for local/other hosts.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
