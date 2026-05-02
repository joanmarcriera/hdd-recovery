#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE="${IMAGE:-hdd-forensics:latest}"

cat <<EOF
T2 john/hashcat/GPU smoke test

Expected commands:
  docker run --rm "$IMAGE" hashcat --version
  docker run --rm "$IMAGE" bash -c 'john --list=formats | grep -i bitcoin'
  docker run --rm "$IMAGE" test -f /usr/share/john/bitcoin2john.py
  docker run --rm --gpus all "$IMAGE" bash -c 'source lib/gpu_check.sh && require_nvidia_gpu'
  docker run --rm "$IMAGE" bash -c 'source lib/gpu_check.sh && require_nvidia_gpu'  # expected to fail clearly on CPU-only host
EOF

docker run --rm "$IMAGE" hashcat --version
docker run --rm "$IMAGE" bash -c 'john --list=formats | grep -i bitcoin'
docker run --rm "$IMAGE" test -f /usr/share/john/bitcoin2john.py
docker run --rm --gpus all "$IMAGE" bash -c 'source lib/gpu_check.sh && require_nvidia_gpu'
if docker run --rm "$IMAGE" bash -c 'source lib/gpu_check.sh && require_nvidia_gpu'; then
  echo "CPU-only GPU check unexpectedly passed" >&2
  exit 1
else
  echo "CPU-only GPU check failed as expected"
fi
