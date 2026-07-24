#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ENV="${GOODMONEYING_APP_ENV_FILE:-$SCRIPT_DIR/env/app.env}"

python3 - "$APP_ENV" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path


def ensure_sslmode_disable(line: str) -> str:
    key, url = line.split("=", 1)
    if "sslmode=require" in url:
        return f"{key}={url.replace('sslmode=require', 'sslmode=disable')}"
    if "sslmode=" in url:
        return line
    separator = "&" if "?" in url else "?"
    return f"{key}={url}{separator}sslmode=disable"


app_env = Path(sys.argv[1])
if not app_env.exists():
    raise SystemExit(f"{app_env} 파일이 없습니다.")

lines = app_env.read_text().splitlines()
database_url_found = False
updated = False
for index, line in enumerate(lines):
    if not line.startswith("GOODMONEYING_DATABASE_URL="):
        continue
    database_url_found = True
    replacement = ensure_sslmode_disable(line)
    if replacement != line:
        lines[index] = replacement
        updated = True
    break

if not database_url_found:
    raise SystemExit("GOODMONEYING_DATABASE_URL 항목이 없습니다.")

if updated:
    app_env.write_text("\n".join(lines) + "\n")

print("GOODMONEYING_DATABASE_URL sslmode=disable 확인 완료")
PY
