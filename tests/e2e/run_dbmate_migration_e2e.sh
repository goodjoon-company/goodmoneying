#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUFFIX="${RANDOM}-$$"
NETWORK="goodmoneying-migration-e2e-${SUFFIX}"
DB_CONTAINER="goodmoneying-migration-db-${SUFFIX}"
API_CONTAINER="goodmoneying-migration-api-${SUFFIX}"
MIGRATION_IMAGE="goodmoneying-migrations:e2e-${SUFFIX}"
API_IMAGE="goodmoneying-api:migration-e2e-${SUFFIX}"
POSTGRES_IMAGE="postgres:17.10"
POSTGRES_USER="goodmoneying"
POSTGRES_PASSWORD="goodmoneying-e2e"
POSTGRES_DB="goodmoneying"
GOODMONEYING_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${DB_CONTAINER}:5432/${POSTGRES_DB}?sslmode=disable"
DBMATE_STRICT=true
DOCKER_CONFIG="${GOODMONEYING_E2E_DOCKER_CONFIG:-$ROOT_DIR/.dev/docker-e2e}"
SNAPSHOT_DIR="$ROOT_DIR/.dev/migration-e2e-${SUFFIX}"
CANONICAL_SCHEMA="$ROOT_DIR/docs/contracts/db/schema.sql"

export GOODMONEYING_DATABASE_URL DBMATE_STRICT DOCKER_CONFIG
export POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB

cleanup() {
  docker rm -f "$API_CONTAINER" >/dev/null 2>&1 || true
  docker rm -fv "$DB_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  docker image rm "$MIGRATION_IMAGE" >/dev/null 2>&1 || true
  docker image rm "$API_IMAGE" >/dev/null 2>&1 || true
  rm -rf "$SNAPSHOT_DIR"
}
trap cleanup EXIT

mkdir -p "$DOCKER_CONFIG" "$SNAPSHOT_DIR"
docker info >/dev/null
docker build -f "$ROOT_DIR/apps/migrations/Dockerfile" -t "$MIGRATION_IMAGE" "$ROOT_DIR"
docker build -f "$ROOT_DIR/apps/api/Dockerfile" -t "$API_IMAGE" "$ROOT_DIR"
docker network create "$NETWORK" >/dev/null
docker run -d \
  --name "$DB_CONTAINER" \
  --network "$NETWORK" \
  -p 127.0.0.1::5432 \
  -e POSTGRES_USER \
  -e POSTGRES_PASSWORD \
  -e POSTGRES_DB \
  "$POSTGRES_IMAGE" >/dev/null

ready=0
for _ in {1..30}; do
  if docker exec "$DB_CONTAINER" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != "1" ]]; then
  printf '오류: E2E PostgreSQL이 준비되지 않았습니다.\n' >&2
  docker logs "$DB_CONTAINER" >&2 || true
  exit 1
fi

docker run --rm \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL \
  -e DBMATE_STRICT \
  "$MIGRATION_IMAGE" \
  --env GOODMONEYING_DATABASE_URL --migrations-dir /db/migrations --no-dump-schema migrate

docker exec "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "INSERT INTO instruments (exchange, market_code, quote_currency, base_asset, display_name) VALUES ('UPBIT', 'KRW-E2E', 'KRW', 'E2E', '마이그레이션 E2E');" \
  >/dev/null

docker run --rm \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL \
  -e DBMATE_STRICT \
  "$MIGRATION_IMAGE" \
  --env GOODMONEYING_DATABASE_URL --migrations-dir /db/migrations --no-dump-schema migrate

version_count="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM schema_migrations;")"
instrument_count="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM instruments WHERE market_code = 'KRW-E2E';")"
timezone="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SHOW timezone;")"

[[ "$version_count" == "1" ]] || { printf '오류: 적용 버전 수=%s\n' "$version_count" >&2; exit 1; }
[[ "$instrument_count" == "1" ]] || { printf '오류: 재적용 후 데이터 행 수=%s\n' "$instrument_count" >&2; exit 1; }
[[ "$timezone" == "Asia/Seoul" ]] || { printf '오류: DB 시간대=%s\n' "$timezone" >&2; exit 1; }

docker run -d \
  --name "$API_CONTAINER" \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL \
  -e GOODMONEYING_OPERATOR_TOKEN=migration-e2e-token \
  "$API_IMAGE" >/dev/null

api_ready=0
for _ in {1..30}; do
  if docker exec "$API_CONTAINER" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" \
    >/dev/null 2>&1; then
    api_ready=1
    break
  fi
  sleep 1
done
if [[ "$api_ready" != "1" ]]; then
  printf '오류: 마이그레이션 완료 DB를 사용하는 API가 준비되지 않았습니다.\n' >&2
  docker logs "$API_CONTAINER" >&2 || true
  exit 1
fi
docker exec "$API_CONTAINER" python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/dashboard/summary', timeout=5)" \
  >/dev/null

docker run --rm \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL \
  -e DBMATE_STRICT \
  -v "$SNAPSHOT_DIR:/output" \
  "$MIGRATION_IMAGE" \
  --env GOODMONEYING_DATABASE_URL --migrations-dir /db/migrations \
  --schema-file /output/schema.sql dump

awk '
  /^-- Dumped from database version / {
    print "-- Dumped from database version (normalized)"
    next
  }
  /^-- Dumped by pg_dump version / {
    print "-- Dumped by pg_dump version (normalized)"
    next
  }
  { print }
' "$SNAPSHOT_DIR/schema.sql" >"$SNAPSHOT_DIR/schema.normalized.sql"
mv "$SNAPSHOT_DIR/schema.normalized.sql" "$SNAPSHOT_DIR/schema.sql"

if [[ "${GOODMONEYING_UPDATE_DB_SNAPSHOT:-0}" == "1" ]]; then
  cp "$SNAPSHOT_DIR/schema.sql" "$CANONICAL_SCHEMA"
else
  diff -u "$CANONICAL_SCHEMA" "$SNAPSHOT_DIR/schema.sql"
fi

host_binding="$(docker port "$DB_CONTAINER" 5432/tcp | head -1)"
host_port="${host_binding##*:}"
(
  cd "$ROOT_DIR"
  GOODMONEYING_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${host_port}/${POSTGRES_DB}?sslmode=disable" \
  GOODMONEYING_LIVE_POSTGRES_TEST=1 \
    uv run pytest -q tests/e2e/test_live_postgres_candle_aggregation.py
)
GOODMONEYING_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${host_port}/${POSTGRES_DB}?sslmode=disable" \
GOODMONEYING_ENV_FILE="$SNAPSHOT_DIR/missing.env" \
GOODMONEYING_DB_SCHEMA_FILE="$SNAPSHOT_DIR/local-fallback-schema.sql" \
GOODMONEYING_DBMATE_DOCKER_CONFIG="$DOCKER_CONFIG" \
GOODMONEYING_FORCE_DOCKER_DB_DUMP=1 \
  "$ROOT_DIR/dev.sh" db dump
diff -u "$SNAPSHOT_DIR/schema.sql" "$SNAPSHOT_DIR/local-fallback-schema.sql"

printf 'dbmate 마이그레이션 E2E 통과: versions=%s data_rows=%s timezone=%s API=200 snapshot=동일 집계상태=동일\n' \
  "$version_count" "$instrument_count" "$timezone"
