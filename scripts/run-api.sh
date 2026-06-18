#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHONPATH=apps/api:apps/worker:packages/shared \
  uv run uvicorn goodmoneying_api.main:app --host 127.0.0.1 --port 8000
