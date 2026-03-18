#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_TAG="${1:-ale-bench-lite-worker:latest}"

docker build -f "${SCRIPT_DIR}/Dockerfile" -t "${IMAGE_TAG}" "${SCRIPT_DIR}"
