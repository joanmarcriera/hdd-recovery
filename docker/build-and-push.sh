#!/usr/bin/env bash
# Build and push the hdd-forensics image to Docker Hub.
#
# Run this on a NATIVE linux/amd64 host (TrueNAS shell, a Linux server, or a
# cloud VM). Building under QEMU emulation on Apple Silicon segfaults CPython
# during some Kali package post-install steps, so emulated builds are not
# supported — use a real amd64 machine or the GitHub Actions workflow instead.
#
# Usage:
#   docker login                       # once, as joanmarcriera
#   ./docker/build-and-push.sh         # tags :latest and :<gitsha>-<date>
#   ./docker/build-and-push.sh v1.2.0  # also tags :v1.2.0
set -Eeuo pipefail

REPO="${IMAGE_REPO:-joanmarcriera/hdd-forensics}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SHA="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
VERSION="${SHA}-$(date -u +%Y%m%d)"
EXTRA_TAG="${1:-}"

ARCH="$(uname -m)"
if [[ "$ARCH" != "x86_64" && "$ARCH" != "amd64" ]]; then
  printf 'WARNING: host arch is %s, not amd64. The TrueNAS app needs an amd64\n' "$ARCH" >&2
  printf '         image; emulated builds of this image segfault. Aborting.\n' >&2
  exit 1
fi

tags=(-t "${REPO}:latest" -t "${REPO}:${VERSION}")
[[ -n "$EXTRA_TAG" ]] && tags+=(-t "${REPO}:${EXTRA_TAG}")

printf 'Building %s (version %s) for linux/amd64...\n' "$REPO" "$VERSION"
docker build \
  --build-arg APP_VERSION="$VERSION" \
  -f docker/Dockerfile \
  "${tags[@]}" \
  .

printf 'Pushing tags...\n'
docker push "${REPO}:latest"
docker push "${REPO}:${VERSION}"
[[ -n "$EXTRA_TAG" ]] && docker push "${REPO}:${EXTRA_TAG}"

printf '\nDone. Pushed:\n  %s:latest\n  %s:%s\n' "$REPO" "$REPO" "$VERSION"
[[ -n "$EXTRA_TAG" ]] && printf '  %s:%s\n' "$REPO" "$EXTRA_TAG"
printf 'Verify after deploy:  curl -s http://<nas-ip>:7788/status | grep version\n'
