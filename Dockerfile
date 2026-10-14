# Hearth's backend, serving the built UI from the same origin, for the NAS
# (docker-compose.nas.yml). The model is not in here and never will be: LM
# Studio runs on the desktop, and this calls it across the LAN.

# --- the UI --------------------------------------------------------------------
FROM node:22.12-bookworm-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- the backend ---------------------------------------------------------------
FROM python:3.12-slim-bookworm

# pg_dump and pg_restore at 17, matching the server, for `make backup` run
# inside the container. Debian's own client is 15, which refuses a 17 server.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-17 \
 && apt-get purge -y curl gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PGBIN=/usr/lib/postgresql/17/bin \
    HEARTH_WEB_DIST=/app/web/dist \
    # Rule 6, again here because this image's environment is not .env's.
    LANGCHAIN_TRACING_V2=false \
    LANGSMITH_TRACING=false \
    ANONYMIZED_TELEMETRY=false \
    DO_NOT_TRACK=1

WORKDIR /app
COPY requirements.txt requirements.lock ./
# The lock is the desktop's, as constraints: it pins what is installed and
# installs nothing itself, so its Windows-only packages are simply unused here.
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock

COPY . .
COPY --from=web /src/web/dist /app/web/dist

RUN useradd --system --uid 10001 hearth
USER hearth

EXPOSE 8000
# Migrations first, so a new image never runs against an old schema. Then the
# server: inside the container on every interface, and the compose file decides
# which of the NAS's addresses publish it — loopback only, for the reverse proxy.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn api.main:app --host 0.0.0.0 --port 8000"]
