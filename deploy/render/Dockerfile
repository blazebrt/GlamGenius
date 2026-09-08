# syntax=docker/dockerfile:1.7
#
# The production image for the Render runtime.
#
# Why this exists alongside backend/Dockerfile
# --------------------------------------------
# backend/Dockerfile is built with `backend/` as its context, so the image it
# produces contains the application and nothing above it. That is correct for
# local development and it is what docker-compose uses.
#
# It is not sufficient for production, because the Phase B controlled
# activation operator deliberately lives OUTSIDE the application:
#
#     scripts/operate_step8i_petrolatum_release.py
#
# It lives there so that `backend/app/**` cannot import `app.knowledge_packs`
# even by accident — a static test in the Step 8I suite enforces exactly that.
# Moving the operator into the application to make packaging easier would
# delete the boundary it exists to create. So this image is built from the
# repository root instead, and preserves the sibling layout the operator
# expects:
#
#     /workspace/
#         backend/          <- the application; WORKDIR
#         scripts/
#             operate_step8i_petrolatum_release.py
#
# The operator resolves `parents[1] / "backend"` onto sys.path, so it runs
# unmodified. No path-neutral change was needed.
#
# Being present is not being invoked
# ----------------------------------
# Nothing here runs the operator. It is not the ENTRYPOINT, not the CMD, not
# a pre-deploy command, not a cron command, not a build step. Production use
# is an explicit one-off job an operator starts by hand, after proving the
# deployed commit is the one a human reviewed. See docs/OPERATIONS.md,
# "Render Production Runtime".
#
# Security properties, carried over from backend/Dockerfile deliberately:
#   * base image pinned by digest (same digest, same update script)
#   * multi-stage: no compiler or build chain in the runtime layer
#   * non-root runtime user (uid/gid 10001)
#   * tini as PID 1, so signals and zombies are handled correctly
#   * no secret baked in; nothing is passed as a build argument
#
# The same caveat as backend/Dockerfile applies: `apt-get upgrade` runs against
# live Debian repositories, so OS package provenance is not deterministic. That
# is the intended trade — it is how a published Debian fix reaches the image
# without editing this file. Do not call this build reproducible.
#
# Build (from the repository root, which is the required context):
#     docker build -f deploy/render/Dockerfile -t glamgenius-production .

# ---------- Stage 1: builder ------------------------------------------------
FROM python@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --upgrade pip setuptools wheel && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt

# ---------- Stage 2: runtime ------------------------------------------------
FROM python@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TMPDIR=/tmp

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip setuptools wheel \
    && pip uninstall -y pip

RUN groupadd --system --gid 10001 app \
    && useradd  --system --uid 10001 --gid 10001 --home /workspace --shell /sbin/nologin app

COPY --from=builder /install /usr/local

# The repository relationship, preserved. Only these two trees are copied:
# the application, and the one root script production operation needs. The
# frontend, .git, tests caches, local .env files and development artefacts are
# excluded by the root .dockerignore.
WORKDIR /workspace
COPY --chown=10001:10001 backend/ /workspace/backend/
COPY --chown=10001:10001 scripts/operate_step8i_petrolatum_release.py /workspace/scripts/

# Exact-commit provenance. The value is supplied at runtime by Render, never
# baked in at build time: a SHA compiled into the image would be a claim about
# provenance that the image itself could not keep honest across a rebuild.
# The entrypoint copies Render's RENDER_GIT_COMMIT into COMMIT_SHA, which both
# workers already record as `service_version` on their heartbeat.
COPY --chown=10001:10001 deploy/render/entrypoint.sh /usr/local/bin/glamgenius-entrypoint
RUN chmod 0555 /usr/local/bin/glamgenius-entrypoint

# Media never uses the local adapter in production — MEDIA_STORAGE_BACKEND is
# supabase and /api/v2/ready refuses to be ready otherwise — so no disk is
# declared and no media root is created here.

# Every backend command in this repository — uvicorn, both workers, the release
# entrypoint and alembic — is documented and tested as being run from backend/.
# Making that the working directory is what lets render.yaml carry the exact
# same command strings the runbook and CLAUDE.md already use.
WORKDIR /workspace/backend

USER 10001

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/glamgenius-entrypoint"]

# Liveness only. This must not depend on PostgreSQL: a database blip should
# not make Docker kill a healthy process. Readiness — which does check the
# database — is /api/v2/ready, and it is Render's external probe, not this one.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v2/health', timeout=3).getcode() == 200 else 1)" || exit 1

# Overridden per service in render.yaml. The default is the API, bound to
# 0.0.0.0 so the container is reachable, on Render's runtime $PORT.
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
