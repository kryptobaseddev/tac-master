#!/usr/bin/env bash
# Build the tac-worker base image for the Podman runtime mode.
#
# Usage:
#   bash deploy/docker/build.sh                  # build tac-worker:latest
#   bash deploy/docker/build.sh v1.0             # build tac-worker:v1.0
#   TAG=v1.0 IMAGE=my-worker bash build.sh       # custom image+tag
#
# Run this as the service user (e.g. krypto) so the resulting image lives
# in the user's rootless podman storage at ~/.local/share/containers/.

set -euo pipefail

IMAGE="${IMAGE:-tac-worker}"
TAG="${TAG:-${1:-latest}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAC_HOME="$(cd "$SCRIPT_DIR/../.." && pwd)"

log() { printf '\033[1;36m[build]\033[0m %s\n' "$*"; }

log "Building $IMAGE:$TAG from $TAC_HOME/deploy/docker/Dockerfile.tac-worker"

# Prefer podman; fall back to docker if present
if command -v podman >/dev/null 2>&1; then
    ENGINE=podman
elif command -v docker >/dev/null 2>&1; then
    ENGINE=docker
else
    echo "error: neither podman nor docker found in PATH" >&2
    exit 1
fi

log "Using $ENGINE"

cd "$TAC_HOME"
"$ENGINE" build \
    --tag "$IMAGE:$TAG" \
    --file deploy/docker/Dockerfile.tac-worker \
    .

log "Built $IMAGE:$TAG"
"$ENGINE" images "$IMAGE:$TAG"

log ""
log "To use this image, set in config/repos.yaml per-repo:"
log "    runtime: podman"
log "    container_image: $IMAGE:$TAG"
