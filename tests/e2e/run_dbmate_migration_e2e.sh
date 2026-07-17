#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUFFIX="${RANDOM}-$$"
NETWORK="goodmoneying-migration-e2e-${SUFFIX}"
DB_CONTAINER="goodmoneying-migration-db-${SUFFIX}"
API_CONTAINER="goodmoneying-migration-api-${SUFFIX}"
UPGRADE_API_CONTAINER="goodmoneying-upgrade-api-${SUFFIX}"
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
EXPECTED_MIGRATION_COUNT="$(find "$ROOT_DIR/docs/contracts/db/migrations" -type f -name '*.sql' | wc -l | tr -d ' ')"

export GOODMONEYING_DATABASE_URL DBMATE_STRICT DOCKER_CONFIG
export POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB

cleanup() {
  docker rm -f "$API_CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$UPGRADE_API_CONTAINER" >/dev/null 2>&1 || true
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

UPGRADE_DB="goodmoneying_upgrade"
UPGRADE_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${DB_CONTAINER}:5432/${UPGRADE_DB}?sslmode=disable"
PRE_004_MIGRATIONS="$SNAPSHOT_DIR/pre-004-migrations"
LEGACY_TAIL_MIGRATIONS="$SNAPSHOT_DIR/legacy-tail-migrations"
PRE_008_MIGRATIONS="$SNAPSHOT_DIR/pre-008-migrations"
mkdir -p "$PRE_004_MIGRATIONS" "$LEGACY_TAIL_MIGRATIONS" "$PRE_008_MIGRATIONS"
cp \
  "$ROOT_DIR/docs/contracts/db/migrations/20260715000100_initial_schema.sql" \
  "$ROOT_DIR/docs/contracts/db/migrations/20260717000100_system_trading_data_foundation.sql" \
  "$ROOT_DIR/docs/contracts/db/migrations/20260717000200_collection_target_state_reason.sql" \
  "$ROOT_DIR/docs/contracts/db/migrations/20260717000300_fetch_manifest_raw_response.sql" \
  "$PRE_004_MIGRATIONS/"
cp \
  "$ROOT_DIR/docs/contracts/db/migrations/20260717000400_coverage_five_states_quality_events.sql" \
  "$ROOT_DIR/docs/contracts/db/migrations/20260717000500_source_orderbook_evidence.sql" \
  "$ROOT_DIR/docs/contracts/db/migrations/20260717000600_p1_review_safety_contracts.sql" \
  "$LEGACY_TAIL_MIGRATIONS/"
cp \
  "$ROOT_DIR/docs/contracts/db/migrations/20260717000700_p1_recovery_readiness.sql" \
  "$PRE_008_MIGRATIONS/"
docker exec "$DB_CONTAINER" createdb -U "$POSTGRES_USER" "$UPGRADE_DB"
docker run --rm \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL="$UPGRADE_DATABASE_URL" \
  -e DBMATE_STRICT \
  -v "$PRE_004_MIGRATIONS:/db/pre-004-migrations:ro" \
  "$MIGRATION_IMAGE" \
  --env GOODMONEYING_DATABASE_URL --migrations-dir /db/pre-004-migrations \
  --no-dump-schema migrate
docker exec "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" -d "$UPGRADE_DB" -c \
  "WITH policy AS (
     INSERT INTO collection_policies (
       exchange, quote_currency, name, default_start_at, priority
     ) VALUES ('UPBIT', 'KRW', 'upgrade-e2e', '2024-01-01T00:00:00Z', 100)
     RETURNING id
   ), market AS (
     INSERT INTO markets (
       exchange, market_code, quote_currency, base_asset,
       korean_name, english_name, first_observed_at, last_observed_at
     ) VALUES (
       'UPBIT', 'KRW-UPGRADE', 'KRW', 'UPGRADE',
       '업그레이드', 'Upgrade', '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z'
     ) RETURNING id
   ), spec AS (
     INSERT INTO collection_target_specs (
       policy_id, market_id, data_type, candle_unit, range_start_at,
       priority, continuous, status
     )
     SELECT policy.id, market.id, 'source_candle', '1m',
            '2024-01-01T00:00:00Z', 100, true, 'active'
     FROM policy, market
     RETURNING id
   )
   INSERT INTO data_quality_events (
     target_spec_id, event_type, previous_status, new_status,
     range_start_at, range_end_at, fingerprint, evidence, detected_at
   )
   SELECT id, 'first-event', 'observed', 'failed',
          '2026-07-17T00:00:00Z'::timestamptz,
          '2026-07-17T00:01:00Z'::timestamptz,
          'same-fingerprint', '{}'::jsonb,
          '2026-07-17T00:01:00Z'::timestamptz
   FROM spec
   UNION ALL
   SELECT id, 'second-event', 'failed', 'observed',
          '2026-07-17T00:01:00Z'::timestamptz,
          '2026-07-17T00:02:00Z'::timestamptz,
          'same-fingerprint', '{}'::jsonb,
          '2026-07-17T00:02:00Z'::timestamptz
   FROM spec;" >/dev/null
docker run --rm \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL="$UPGRADE_DATABASE_URL" \
  -e DBMATE_STRICT \
  -v "$LEGACY_TAIL_MIGRATIONS:/db/legacy-tail-migrations:ro" \
  "$MIGRATION_IMAGE" \
  --env GOODMONEYING_DATABASE_URL --migrations-dir /db/legacy-tail-migrations \
  --no-dump-schema migrate
docker run --rm \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL="$UPGRADE_DATABASE_URL" \
  -e DBMATE_STRICT \
  -v "$PRE_008_MIGRATIONS:/db/pre-008-migrations:ro" \
  "$MIGRATION_IMAGE" \
  --env GOODMONEYING_DATABASE_URL --migrations-dir /db/pre-008-migrations \
  --no-dump-schema migrate
docker exec "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" -d "$UPGRADE_DB" -c \
  "UPDATE p1_audit_recovery_gate
   SET confirmed_at = now(), confirmed_by = NULL,
       backup_reference = NULL, updated_at = now()
   WHERE singleton;" >/dev/null
docker exec "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" -d "$UPGRADE_DB" -c \
  "WITH instrument AS (
     INSERT INTO instruments (
       exchange, market_code, quote_currency, base_asset, display_name
     ) VALUES ('UPBIT', 'KRW-P2-UPGRADE', 'KRW', 'P2UPGRADE', 'P2 업그레이드')
     RETURNING id
   )
   INSERT INTO source_candles (
     instrument_id, source, candle_unit, candle_start_at,
     open_price, high_price, low_price, close_price,
     trade_volume, trade_amount, collected_at
   )
   SELECT id, 'UPBIT', '1m', '2026-07-17T07:00:00Z',
          100, 101, 99, 100, 1, 100, '2026-07-17T07:00:01Z'
   FROM instrument;" >/dev/null
docker run --rm \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL="$UPGRADE_DATABASE_URL" \
  -e DBMATE_STRICT \
  "$MIGRATION_IMAGE" \
  --env GOODMONEYING_DATABASE_URL --migrations-dir /db/migrations --no-dump-schema migrate
upgrade_event_count="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" \
  -d "$UPGRADE_DB" -c \
  "SELECT count(*) FROM data_quality_events WHERE fingerprint = 'same-fingerprint';")"
upgrade_event_states="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" \
  -d "$UPGRADE_DB" -c \
  "SELECT string_agg(event_type || ':' || previous_status || '>' || new_status, ',' ORDER BY detected_at)
  FROM data_quality_events WHERE fingerprint = 'same-fingerprint';")"
upgrade_revision_state="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" \
  -d "$UPGRADE_DB" -c \
  "SELECT count(*)::text || ':' || bool_and(candle.market_id IS NOT NULL)::text
   FROM source_candles candle
   JOIN source_candle_revisions revision ON revision.source_candle_id = candle.id
   JOIN instruments instrument ON instrument.id = candle.instrument_id
   WHERE instrument.market_code = 'KRW-P2-UPGRADE';")"
[[ "$upgrade_event_count" == "1" ]] || {
  printf '오류: 구버전 004 적용 뒤 남은 품질 이벤트 행 수=%s\n' "$upgrade_event_count" >&2
  exit 1
}
[[ "$upgrade_event_states" == "first-event:available>missing" ]] || {
  printf '오류: 구버전 004 품질 이벤트 상태 변환=%s\n' "$upgrade_event_states" >&2
  exit 1
}
[[ "$upgrade_revision_state" == "1:true" ]] || {
  printf '오류: 009 업그레이드 원천 개정 백필 상태=%s\n' "$upgrade_revision_state" >&2
  exit 1
}

upgrade_recovery_state="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" \
  -d "$UPGRADE_DB" -c \
  "SELECT recovery_required::text || ':' || (confirmed_at IS NOT NULL)::text
   FROM p1_audit_recovery_gate WHERE singleton;")"
[[ "$upgrade_recovery_state" == "true:false" ]] || {
  printf '오류: 구버전 적용 DB 복구 게이트=%s\n' "$upgrade_recovery_state" >&2
  exit 1
}
if docker exec "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" -d "$UPGRADE_DB" -c \
  "UPDATE p1_audit_recovery_gate
   SET confirmed_at = now(), confirmed_by = NULL,
       backup_reference = NULL, updated_at = now()
   WHERE singleton;" >/dev/null 2>&1; then
  printf '오류: 008 이후 불완전 복구 확인값이 저장됐습니다.\n' >&2
  exit 1
fi
if docker run --rm --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL="$UPGRADE_DATABASE_URL" "$API_IMAGE" \
  uv run --no-sync python -c "from goodmoneying_shared.data_foundation_repository import PostgresDataFoundationRepository; import os; PostgresDataFoundationRepository(os.environ['GOODMONEYING_DATABASE_URL']).assert_runtime_ready()" \
  >/dev/null 2>&1; then
  printf '오류: 복구 미확인 DB 준비성 검사가 통과했습니다.\n' >&2
  exit 1
fi
docker exec "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$UPGRADE_DB" -c \
  "UPDATE p1_audit_recovery_gate
   SET confirmed_at = now(), confirmed_by = 'migration-e2e',
       backup_reference = 'backup://migration-e2e', updated_at = now()
   WHERE singleton;" >/dev/null
docker run --rm --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL="$UPGRADE_DATABASE_URL" "$API_IMAGE" \
  uv run --no-sync python -c "from goodmoneying_shared.data_foundation_repository import PostgresDataFoundationRepository; import os; PostgresDataFoundationRepository(os.environ['GOODMONEYING_DATABASE_URL']).assert_runtime_ready()"

upgrade_seed_count="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" \
  -d "$UPGRADE_DB" -c \
  "SELECT count(*) FROM indicator_invalidations invalidation
   JOIN instruments instrument ON instrument.id=invalidation.instrument_id
   WHERE instrument.market_code='KRW-P2-UPGRADE';")"
[[ "$upgrade_seed_count" == "1" ]] || {
  printf '오류: P2-3 업그레이드 bounded 초기 invalidation 수=%s\n' "$upgrade_seed_count" >&2
  exit 1
}
upgrade_indicator_processed="$(docker run --rm --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL="$UPGRADE_DATABASE_URL" "$API_IMAGE" \
  uv run --no-sync python -c \
  "import os; from goodmoneying_shared.indicator_store import run_next_indicator_invalidation; from goodmoneying_shared.postgres_repository import PostgresOperationsRepository; repository=PostgresOperationsRepository(os.environ['GOODMONEYING_DATABASE_URL']); print(run_next_indicator_invalidation(repository, 'migration-upgrade-worker'))")"
[[ "$upgrade_indicator_processed" -gt 0 ]] || {
  printf '오류: P2-3 업그레이드 초기 지표 처리 행 수=%s\n' "$upgrade_indicator_processed" >&2
  exit 1
}
upgrade_instrument_id="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" \
  -d "$UPGRADE_DB" -c \
  "SELECT id FROM instruments WHERE market_code='KRW-P2-UPGRADE';")"
docker run -d \
  --name "$UPGRADE_API_CONTAINER" \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL="$UPGRADE_DATABASE_URL" \
  -e GOODMONEYING_RUNTIME_MODE=production \
  -e GOODMONEYING_OPERATOR_TOKEN=migration-e2e-token \
  "$API_IMAGE" >/dev/null
upgrade_api_ready=0
for _ in {1..30}; do
  if docker exec "$UPGRADE_API_CONTAINER" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" \
    >/dev/null 2>&1; then
    upgrade_api_ready=1
    break
  fi
  sleep 1
done
[[ "$upgrade_api_ready" == "1" ]] || {
  printf '오류: P2-3 업그레이드 API가 준비되지 않았습니다.\n' >&2
  docker logs "$UPGRADE_API_CONTAINER" >&2 || true
  exit 1
}
upgrade_indicator_items="$(docker exec "$UPGRADE_API_CONTAINER" python -c \
  "import json, urllib.parse, urllib.request; query=urllib.parse.urlencode({'unit':'1m','from':'2026-07-17T06:59:00Z','to':'2026-07-17T07:01:00Z','asOf':'2026-07-18T00:00:00Z'}); response=json.load(urllib.request.urlopen('http://127.0.0.1:8000/v1/instruments/${upgrade_instrument_id}/indicators?'+query, timeout=5)); print(len(response['items']))")"
[[ "$upgrade_indicator_items" == "1" ]] || {
  printf '오류: P2-3 업그레이드 REST 지표 행 수=%s\n' "$upgrade_indicator_items" >&2
  exit 1
}
docker rm -f "$UPGRADE_API_CONTAINER" >/dev/null

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
fresh_recovery_required="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT recovery_required FROM p1_audit_recovery_gate WHERE singleton;")"

[[ "$version_count" == "$EXPECTED_MIGRATION_COUNT" ]] || {
  printf '오류: 적용 버전 수=%s 예상=%s\n' "$version_count" "$EXPECTED_MIGRATION_COUNT" >&2
  exit 1
}
[[ "$instrument_count" == "1" ]] || { printf '오류: 재적용 후 데이터 행 수=%s\n' "$instrument_count" >&2; exit 1; }
[[ "$timezone" == "UTC" ]] || { printf '오류: DB 시간대=%s\n' "$timezone" >&2; exit 1; }
[[ "$fresh_recovery_required" == "f" ]] || { printf '오류: 신규 DB 복구 게이트=%s\n' "$fresh_recovery_required" >&2; exit 1; }

docker restart "$DB_CONTAINER" >/dev/null
restart_ready=0
for _ in {1..30}; do
  if docker exec "$DB_CONTAINER" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    >/dev/null 2>&1; then
    restart_ready=1
    break
  fi
  sleep 1
done
if [[ "$restart_ready" != "1" ]]; then
  printf '오류: 재시작 후 PostgreSQL이 준비되지 않았습니다.\n' >&2
  docker logs "$DB_CONTAINER" >&2 || true
  exit 1
fi
version_count="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM schema_migrations;")"
instrument_count="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM instruments WHERE market_code = 'KRW-E2E';")"
timezone="$(docker exec "$DB_CONTAINER" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SHOW timezone;")"
[[ "$version_count" == "$EXPECTED_MIGRATION_COUNT" ]] || {
  printf '오류: 재시작 후 적용 버전 수=%s 예상=%s\n' "$version_count" "$EXPECTED_MIGRATION_COUNT" >&2
  exit 1
}
[[ "$instrument_count" == "1" ]] || { printf '오류: 재시작 후 데이터 행 수=%s\n' "$instrument_count" >&2; exit 1; }
[[ "$timezone" == "UTC" ]] || { printf '오류: 재시작 후 DB 시간대=%s\n' "$timezone" >&2; exit 1; }

docker run -d \
  --name "$API_CONTAINER" \
  --network "$NETWORK" \
  -e GOODMONEYING_DATABASE_URL \
  -e GOODMONEYING_RUNTIME_MODE=production \
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
    uv run pytest -q \
      tests/e2e/test_live_postgres_data_foundation.py \
      tests/e2e/test_live_postgres_candle_aggregation.py \
      tests/e2e/test_live_postgres_incremental_candle_aggregation.py \
      tests/e2e/test_live_postgres_versioned_indicators.py \
      tests/e2e/test_live_postgres_source_evidence.py \
      tests/e2e/test_live_postgres_microstructure.py
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
