#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
npm --workspace apps/web run dev -- --host 127.0.0.1 --port 5173
