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

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

# Cloud Run sets $PORT; default to 8080 for local/other hosts.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
