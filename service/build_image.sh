#!/usr/bin/env bash
# Build and verify the sandbox image locally, the same way CI does. Run from the repo root:
#
#   service/build_image.sh            # reuses .tmp/taxonomy if it exists
#   REWARM=1 service/build_image.sh   # fetch the taxonomy cache again
#
# Needs Docker with buildx, and a Python with Arelle's requirements (PYTHON, default .tmp/venv/bin/python).
set -euo pipefail

PYTHON="${PYTHON:-.tmp/venv/bin/python}"
TAXONOMY_DIR=.tmp/taxonomy
TAG="${TAG:-arelle-sandbox:dev}"

if [[ ! -f service/Dockerfile ]]; then
  echo "Run from the repository root." >&2
  exit 1
fi

if [[ "${REWARM:-}" == 1 ]]; then
  rm -rf "$TAXONOMY_DIR"
fi
if [[ ! -f "$TAXONOMY_DIR/cache-manifest.json" ]]; then
  rm -rf "$TAXONOMY_DIR"
  mkdir -p .tmp/tmp
  TMPDIR=.tmp/tmp "$PYTHON" service/warm_cache.py \
    --dest "$TAXONOMY_DIR/cache" --report "$TAXONOMY_DIR/cache-manifest.json" \
    --lock service/taxonomy-cache.lock.json
else
  # A cache kept from an earlier run must still match the committed pin list.
  "$PYTHON" service/warm_cache.py --check-report "$TAXONOMY_DIR/cache-manifest.json" \
    --lock service/taxonomy-cache.lock.json
fi

docker buildx build --platform linux/amd64 --load \
  -f service/Dockerfile \
  --build-context "taxonomy=$TAXONOMY_DIR" \
  --build-arg "SERVICE_VERSION=$(tr -d '[:space:]' < service/VERSION)-local" \
  --build-arg "GIT_SHA=$(git -c safe.directory='*' rev-parse HEAD)" \
  --build-arg "TAXONOMY_CACHE_SHA256=$(sha256sum "$TAXONOMY_DIR/cache-manifest.json" | cut -d' ' -f1)" \
  -t "$TAG" .

docker run --rm --network none --platform linux/amd64 "$TAG" \
  /opt/venv/bin/python /app/service/verify_image.py
