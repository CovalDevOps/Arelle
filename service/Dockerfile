# syntax=docker/dockerfile:1.7
#
# Arelle validation image for Vercel Sandbox (one microVM per job).
#
# The taxonomy files come from Arelle's own web cache, filled beforehand by service/warm_cache.py and passed
# in as the named build context "taxonomy" (a directory holding cache/ and cache-manifest.json). The build
# itself downloads no taxonomies, so rebuilding for a code change reuses the exact same files. The warm-up
# fails unless every file matches service/taxonomy-cache.lock.json.
#
#   .tmp/venv/bin/python service/warm_cache.py --dest .tmp/taxonomy/cache --report .tmp/taxonomy/cache-manifest.json \
#       --lock service/taxonomy-cache.lock.json
#   docker buildx build --platform linux/amd64 -f service/Dockerfile \
#       --build-context taxonomy=.tmp/taxonomy -t arelle-sandbox:dev .
#
# (service/build_image.sh does both.)
#
# Notes for Vercel Sandbox:
# - Sandbox ignores ENTRYPOINT and CMD; the caller starts jobs with runCommand(), using absolute paths
#   (/opt/venv/bin/python /app/...).
# - The docs don't say whether image ENV applies to runCommand. Callers should pass the env vars below
#   explicitly too, until this is confirmed.
# - Jobs run Arelle with: --internetConnectivity offline --cacheDirectory /opt/arelle/cache --httpsRedirectCache
# - Layers are ordered from least to most often changed: dependencies, taxonomy cache, code.

ARG PYTHON_VERSION=3.14.7

## ---------- dependencies: venv with trimmed requirements, versions pinned by upstream ----------
FROM python:${PYTHON_VERSION}-slim AS deps
COPY requirements.txt /build/constraints.txt
COPY service/requirements.txt /build/requirements.txt
RUN python -m venv /opt/venv \
  && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
  && /opt/venv/bin/pip install --no-cache-dir -c /build/constraints.txt -r /build/requirements.txt

## ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH \
    ARELLE_APP_DIR=/app \
    ARELLE_CACHE_DIR=/opt/arelle/cache \
    ARELLE_CACHE_MANIFEST=/opt/arelle/cache-manifest.json \
    XDG_CONFIG_HOME=/var/lib/arelle \
    ARELLE_WORK_OFFLINE=true

# Writable for whichever user the sandbox runs commands as (not documented by Vercel).
RUN mkdir -p /var/lib/arelle /work && chmod 1777 /var/lib/arelle /work

COPY --from=deps /opt/venv /opt/venv

# Read-only for jobs: offline Arelle only reads the cache, and a job must not be able to alter it for later use
# of the same image.
COPY --from=taxonomy --chmod=755 cache /opt/arelle/cache
COPY --from=taxonomy --chmod=444 cache-manifest.json /opt/arelle/cache-manifest.json

WORKDIR /app
COPY arelleCmdLine.py ./
COPY arelle ./arelle
COPY service ./service

# Build metadata, also read by jobs so results record which image produced them.
ARG SERVICE_VERSION=dev
ARG GIT_SHA=unknown
ARG TAXONOMY_CACHE_SHA256=unknown
LABEL org.opencontainers.image.title="arelle-sandbox" \
      org.opencontainers.image.version="${SERVICE_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      com.coval.taxonomy-cache-manifest-sha256="${TAXONOMY_CACHE_SHA256}"
RUN printf '{"version": "%s", "git_sha": "%s", "taxonomy_cache_manifest_sha256": "%s"}\n' \
      "${SERVICE_VERSION}" "${GIT_SHA}" "${TAXONOMY_CACHE_SHA256}" > /app/BUILD_INFO.json \
  && python -m compileall -q /app/arelle /app/service /app/arelleCmdLine.py
