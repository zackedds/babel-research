#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SCRIPT_DIR}/ale_bench_lite.yaml"
OUTPUT_DIR="./ale_bench_runs"

PROBLEMS=(ahc008 ahc011 ahc015 ahc016 ahc024 ahc025 ahc026 ahc027 ahc039 ahc046)

for problem_id in "${PROBLEMS[@]}"; do
  echo "=========================================="
  echo "Running ${problem_id}"
  echo "=========================================="
  uv run "${SCRIPT_DIR}/run_ale_bench_problem.py" \
    --config "${CONFIG}" \
    --problem-id "${problem_id}" \
    --output-dir "${OUTPUT_DIR}" \
    "$@"
done
