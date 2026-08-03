# syntax=docker/dockerfile:1.7
#
# Reproducible backend image for GlamGenius (Fix 4, WP2).
#
# What "reproducible" means here:
#
#   * pinned base image (python:3.11.10-slim, digest updated by
#     scripts/update_service_digests.sh)
#   * pinned Python dependencies via requirements.txt exact versions
#   * non-root runtime user
#   * no shell history, no build tooling in the final image
#
# Build:
#     docker build -f backend/Dockerfile -t glamgenius-backend backend/
#
# The compose files rely on this Dockerfile via `context: ./backend`, so a
# host with only Docker installed (no Python, no Node) can bring up the whole
# stack — see scripts/verify_clean_environment.sh.

# ---------- Stage 1: builder ------------------------------------------------
FROM python:3.11.10-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Build-only dependencies. asyncpg wheels are pre-built for python:3.11-slim,
# but bcrypt / cffi occasionally need a compiler on ARM. Installed only in
# the builder stage so the final image has no compiler on disk.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# ---------- Stage 2: runtime ------------------------------------------------
FROM python:3.11.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Runtime OS dependencies: curl for the container health check that the
# compose files use, and tini so PID 1 handles signals correctly. Neither
# ships a compiler.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. Uid/gid are stable across builds so the mounted media volume
# keeps consistent ownership between rebuilds.
RUN groupadd --system --gid 10001 app \
    && useradd  --system --uid 10001 --gid 10001 --home /app --shell /sbin/nologin app

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=10001:10001 . .

# Where the local media adapter writes in development. Overridden by a volume
# in docker-compose so uploads survive a container rebuild.
ENV MEDIA_LOCAL_ROOT=/data/media
RUN mkdir -p /data/media && chown -R 10001:10001 /data/media

USER 10001

EXPOSE 8000

# tini reaps zombies and forwards signals so `docker stop` is graceful. The
# migration is executed once at start-up; if it fails the container refuses
# to serve traffic on the health check.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "alembic upgrade head && uvicorn server:app --host 0.0.0.0 --port 8000"]
