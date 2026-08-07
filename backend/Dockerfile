# syntax=docker/dockerfile:1.7
#
# Reproducible backend image for GlamGenius (Fix 4, WP2).
#
# What "reproducible" means here:
#
#   * pinned base image (python:3.11.15-slim-trixie, digest updated by
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
FROM python@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Build-only dependencies. asyncpg wheels are pre-built for python:3.11-slim,
# but bcrypt / cffi occasionally need a compiler on ARM. Installed only in
# the builder stage so the final image has no compiler on disk.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt

# ---------- Stage 2: runtime ------------------------------------------------
FROM python@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TMPDIR=/tmp

# Runtime OS dependencies: tini so PID 1 handles signals correctly.
# The healthcheck uses the Python standard library.
# No compiler or build tooling is present in the runtime.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip setuptools wheel \
    && pip uninstall -y pip

# Non-root user. Uid/gid are stable across builds so the mounted media volume
# keeps consistent ownership between rebuilds.
RUN groupadd --system --gid 10001 app \
    && useradd  --system --uid 10001 --gid 10001 --home /app --shell /sbin/nologin app

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=10001:10001 . .

# Emit build version/commit SHA safely
ARG COMMIT_SHA=unknown
ENV COMMIT_SHA=${COMMIT_SHA}

# Where the local media adapter writes in development. Overridden by a volume
# in docker-compose so uploads survive a container rebuild.
ENV MEDIA_LOCAL_ROOT=/data/media
RUN mkdir -p /data/media && chown -R 10001:10001 /data/media

USER 10001

EXPOSE 8000

# tini reaps zombies and forwards signals so `docker stop` is graceful.
ENTRYPOINT ["/usr/bin/tini", "--"]

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').getcode() == 200 else 1)" || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
