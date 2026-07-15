#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

load_env_file() {
  local env_file="${GOODMONEYING_ENV_FILE:-"$ROOT_DIR/.env"}"
  [[ -f "$env_file" ]] || return 0

  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="$(trim "$line")"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" == export[[:space:]]* ]]; then
      line="$(trim "${line#export}")"
    fi
    [[ "$line" == *=* ]] || continue

    key="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi

    if [[ -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done <"$env_file"
}

load_env_file

RUNTIME_DIR="${GOODMONEYING_DEV_DIR:-"$ROOT_DIR/.dev"}"
PID_DIR="$RUNTIME_DIR/pids"
LOG_DIR="$RUNTIME_DIR/logs"

API_HOST="${GOODMONEYING_API_HOST:-127.0.0.1}"
API_PORT="${GOODMONEYING_API_PORT:-8000}"
WEB_HOST="${GOODMONEYING_WEB_HOST:-127.0.0.1}"
WEB_PORT="${GOODMONEYING_WEB_PORT:-5173}"
UPBIT_GATEWAY_HOST="${GOODMONEYING_UPBIT_GATEWAY_HOST:-127.0.0.1}"
UPBIT_GATEWAY_PORT="${GOODMONEYING_UPBIT_GATEWAY_PORT:-8001}"
POSTGRES_PORT="${GOODMONEYING_POSTGRES_PORT:-5432}"
OPERATOR_TOKEN="${GOODMONEYING_OPERATOR_TOKEN:-local-dev-token}"
DATABASE_URL="${GOODMONEYING_DATABASE_URL:-postgresql://goodmoneying:goodmoneying@127.0.0.1:${POSTGRES_PORT}/goodmoneying?sslmode=disable}"
APP_TIMEZONE="${GOODMONEYING_TIMEZONE:-Asia/Seoul}"
REALTIME_COLLECTION_INTERVAL_SECONDS="${GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS:-60}"
BACKFILL_POLL_SECONDS="${GOODMONEYING_BACKFILL_POLL_SECONDS:-10}"
BACKFILL_BATCH_SIZE="${GOODMONEYING_BACKFILL_BATCH_SIZE:-3000}"
AGGREGATION_POLL_SECONDS="${GOODMONEYING_AGGREGATION_POLL_SECONDS:-5}"
LOG_LEVEL="${GOODMONEYING_LOG_LEVEL:-INFO}"
PYTHON_BIN="${GOODMONEYING_PYTHON_BIN:-"$ROOT_DIR/.venv/bin/python"}"
DBMATE_BIN="${GOODMONEYING_DBMATE_BIN:-"$ROOT_DIR/node_modules/.bin/dbmate"}"
DB_MIGRATIONS_DIR="$ROOT_DIR/docs/contracts/db/migrations"
DB_SCHEMA_FILE="${GOODMONEYING_DB_SCHEMA_FILE:-"$ROOT_DIR/docs/contracts/db/schema.sql"}"
DBMATE_DOCKER_IMAGE="ghcr.io/amacneil/dbmate:2.34.1"
DBMATE_DOCKER_CONFIG="${GOODMONEYING_DBMATE_DOCKER_CONFIG:-"$RUNTIME_DIR/docker-dbmate"}"
DB_BASELINE_VERSION="20260715000100"
export TZ="$APP_TIMEZONE"
export PGTZ="$APP_TIMEZONE"

usage() {
  cat <<'USAGE'
사용법:
  ./dev.sh
  ./dev.sh status

  ./dev.sh infra start [postgres|all]
  ./dev.sh infra stop [postgres|all]
  ./dev.sh infra restart [postgres|all]
  ./dev.sh infra status [postgres|all]

  ./dev.sh db new <설명>
  ./dev.sh db migrate
  ./dev.sh db status
  ./dev.sh db dump
  ./dev.sh db rollback

  ./dev.sh app start [api|web|upbit-gateway|realtime-collection-worker|backfill-collection-worker|candle-aggregation-worker|all]
  ./dev.sh app stop [api|web|upbit-gateway|realtime-collection-worker|backfill-collection-worker|candle-aggregation-worker|all]
  ./dev.sh app restart [api|web|upbit-gateway|realtime-collection-worker|backfill-collection-worker|candle-aggregation-worker|all]
  ./dev.sh app status [api|web|upbit-gateway|realtime-collection-worker|backfill-collection-worker|candle-aggregation-worker|all]

  ./dev.sh logs [api|web|upbit-gateway|realtime-collection-worker|backfill-collection-worker|candle-aggregation-worker]

설명:
  infra 는 Podman Compose 로 PostgreSQL 을 관리한다.
  db 는 dbmate 로 버전 DB 마이그레이션을 관리한다.
  app 은 로컬 개발 프로세스로 실행한다. API 는 기본적으로 PostgreSQL 을 바라본다.
  app start 와 app restart 는 DB 마이그레이션 성공 후 앱을 시작한다.
  루트 .env 파일이 있으면 자동으로 읽는다. 셸 환경변수는 .env 값보다 우선한다.

기본 endpoint:
  Web: http://127.0.0.1:5173/
  API: http://127.0.0.1:8000
  Upbit Gateway: http://127.0.0.1:8001
  Health: http://127.0.0.1:8000/health

주요 환경변수:
  GOODMONEYING_ENV_FILE
  GOODMONEYING_DATABASE_URL
  GOODMONEYING_OPERATOR_TOKEN
  GOODMONEYING_API_PORT
  GOODMONEYING_WEB_PORT
  GOODMONEYING_UPBIT_GATEWAY_PORT
  GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS
  GOODMONEYING_BACKFILL_POLL_SECONDS
  GOODMONEYING_BACKFILL_BATCH_SIZE
  GOODMONEYING_AGGREGATION_POLL_SECONDS
  GOODMONEYING_LOG_LEVEL
  GOODMONEYING_PYTHON_BIN
  GOODMONEYING_DBMATE_BIN
USAGE
}

ensure_runtime_dirs() {
  mkdir -p "$PID_DIR" "$LOG_DIR"
}

print_error() {
  printf '오류: %s\n' "$*" >&2
}

require_dbmate() {
  if [[ ! -x "$DBMATE_BIN" ]]; then
    print_error "dbmate 실행 파일을 찾을 수 없습니다. 프로젝트 루트에서 'npm install'을 실행하세요."
    return 1
  fi
}

run_dbmate() {
  require_dbmate
  GOODMONEYING_DATABASE_URL="$DATABASE_URL" DBMATE_STRICT=true "$DBMATE_BIN" \
    --env GOODMONEYING_DATABASE_URL \
    --migrations-dir "$DB_MIGRATIONS_DIR" \
    --schema-file "$DB_SCHEMA_FILE" \
    "$@"
}

require_schema_dumper() {
  if [[ -n "${GOODMONEYING_DBMATE_BIN:-}" ]] \
    || { [[ "${GOODMONEYING_FORCE_DOCKER_DB_DUMP:-0}" != "1" ]] \
      && command -v pg_dump >/dev/null 2>&1; }; then
    return 0
  fi
  mkdir -p "$DBMATE_DOCKER_CONFIG"
  if ! command -v docker >/dev/null 2>&1 \
    || ! DOCKER_CONFIG="$DBMATE_DOCKER_CONFIG" docker info >/dev/null 2>&1; then
    print_error "스키마 덤프에 필요한 pg_dump 또는 실행 중인 Docker를 찾을 수 없습니다."
    return 1
  fi
  if ! DOCKER_CONFIG="$DBMATE_DOCKER_CONFIG" docker image inspect "$DBMATE_DOCKER_IMAGE" >/dev/null 2>&1; then
    DOCKER_CONFIG="$DBMATE_DOCKER_CONFIG" docker pull "$DBMATE_DOCKER_IMAGE"
  fi
}

normalize_schema_snapshot() {
  local snapshot="$1"
  local normalized="${snapshot}.normalized"
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
  ' "$snapshot" >"$normalized"
  mv "$normalized" "$snapshot"
}

database_url_for_docker() {
  local url="$DATABASE_URL"
  url="${url//@127.0.0.1:/@host.docker.internal:}"
  url="${url//\/\/127.0.0.1:/\/\/host.docker.internal:}"
  url="${url//@127.0.0.1\//@host.docker.internal\/}"
  url="${url//\/\/127.0.0.1\//\/\/host.docker.internal\/}"
  url="${url//@localhost:/@host.docker.internal:}"
  url="${url//\/\/localhost:/\/\/host.docker.internal:}"
  url="${url//@localhost\//@host.docker.internal\/}"
  url="${url//\/\/localhost\//\/\/host.docker.internal\/}"
  url="${url//@\[::1\]:/@host.docker.internal:}"
  url="${url//\/\/\[::1\]:/\/\/host.docker.internal:}"
  url="${url//@\[::1\]\//@host.docker.internal\/}"
  url="${url//\/\/\[::1\]\//\/\/host.docker.internal\/}"
  printf '%s\n' "$url"
}

dump_schema() {
  local schema_dir schema_name docker_database_url docker_operating_system docker_os_type
  local -a docker_network_args
  require_schema_dumper
  if [[ -n "${GOODMONEYING_DBMATE_BIN:-}" ]] \
    || { [[ "${GOODMONEYING_FORCE_DOCKER_DB_DUMP:-0}" != "1" ]] \
      && command -v pg_dump >/dev/null 2>&1; }; then
    run_dbmate dump
    [[ -f "$DB_SCHEMA_FILE" ]] && normalize_schema_snapshot "$DB_SCHEMA_FILE"
    return
  fi

  schema_dir="$(cd "$(dirname "$DB_SCHEMA_FILE")" && pwd)"
  schema_name="$(basename "$DB_SCHEMA_FILE")"
  docker_operating_system="$(
    DOCKER_CONFIG="$DBMATE_DOCKER_CONFIG" docker info --format '{{.OperatingSystem}}'
  )"
  docker_os_type="$(
    DOCKER_CONFIG="$DBMATE_DOCKER_CONFIG" docker info --format '{{.OSType}}'
  )"
  if [[ "$docker_operating_system" == "Docker Desktop" ]]; then
    docker_database_url="$(database_url_for_docker)"
    docker_network_args=(--add-host host.docker.internal:host-gateway)
  elif [[ "$docker_os_type" == "linux" ]]; then
    docker_database_url="$DATABASE_URL"
    docker_network_args=(--network host)
  else
    print_error "지원하지 않는 Docker 운영체제입니다: ${docker_operating_system} (${docker_os_type})"
    return 1
  fi
  GOODMONEYING_DATABASE_URL="$docker_database_url" DBMATE_STRICT=true \
    DOCKER_CONFIG="$DBMATE_DOCKER_CONFIG" docker run --rm \
    "${docker_network_args[@]}" \
    -e GOODMONEYING_DATABASE_URL \
    -e DBMATE_STRICT \
    -v "$schema_dir:/output" \
    "$DBMATE_DOCKER_IMAGE" \
    --env GOODMONEYING_DATABASE_URL \
    --migrations-dir /db/migrations \
    --schema-file "/output/$schema_name" \
    dump
  normalize_schema_snapshot "$DB_SCHEMA_FILE"
}

reject_baseline_rollback() {
  local status latest_version
  status="$(run_dbmate status)"
  latest_version="$(printf '%s\n' "$status" | sed -n 's/^\[X\] \([0-9][0-9]*\).*/\1/p' | tail -1)"
  if [[ "$latest_version" == "$DB_BASELINE_VERSION" ]]; then
    print_error "기준선 마이그레이션은 rollback할 수 없습니다. 새 순방향 마이그레이션을 작성하세요."
    return 1
  fi
}

db_command() {
  local action="${1:-status}"
  shift || true

  case "$action" in
    new)
      if [[ $# -eq 0 ]]; then
        print_error "새 마이그레이션 설명이 필요합니다. 예: './dev.sh db new add_user_preferences'"
        return 2
      fi
      run_dbmate new "$*"
      ;;
    migrate)
      require_schema_dumper
      run_dbmate --no-dump-schema migrate
      dump_schema
      ;;
    status) run_dbmate status ;;
    dump) dump_schema ;;
    rollback)
      reject_baseline_rollback
      require_schema_dumper
      run_dbmate --no-dump-schema rollback
      dump_schema
      ;;
    *) print_error "알 수 없는 db 명령: $action"; usage; return 2 ;;
  esac
}

podman_compose() {
  if ! command -v podman >/dev/null 2>&1; then
    print_error "podman 명령을 찾을 수 없습니다."
    return 1
  fi
  if podman compose version >/dev/null 2>&1; then
    podman compose "$@"
    return
  fi
  if command -v podman-compose >/dev/null 2>&1; then
    podman-compose "$@"
    return
  fi
  print_error "podman compose 또는 podman-compose 를 찾을 수 없습니다."
  return 1
}

database_host_and_port() {
  local url="${DATABASE_URL%%\?*}"
  local authority host port

  case "$url" in
    postgresql://* | postgres://*) ;;
    *)
      printf '127.0.0.1\t%s\n' "$POSTGRES_PORT"
      return 0
      ;;
  esac

  authority="${url#*://}"
  authority="${authority%%/*}"
  authority="${authority##*@}"

  if [[ "$authority" == \[* ]]; then
    host="${authority%%]*}"
    host="${host#\[}"
    port="${authority#*]}"
    port="${port#:}"
  elif [[ "$authority" == *:* ]]; then
    host="${authority%%:*}"
    port="${authority##*:}"
  else
    host="$authority"
    port="5432"
  fi

  if [[ -z "$host" || ! "$port" =~ ^[0-9]+$ ]]; then
    print_error "GOODMONEYING_DATABASE_URL에서 PostgreSQL 호스트와 포트를 해석할 수 없습니다."
    return 1
  fi

  printf '%s\t%s\n' "$host" "$port"
}

require_local_database_port() {
  local database_target host port
  database_target="$(database_host_and_port)" || return 1
  host="${database_target%%$'\t'*}"
  port="${database_target#*$'\t'}"

  case "$host" in
    127.0.0.1 | localhost | ::1) ;;
    *) return 0 ;;
  esac

  if ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    print_error "로컬 PostgreSQL 포트 ${port} 이 열려 있지 않습니다. .env의 GOODMONEYING_DATABASE_URL을 확인하거나 './dev.sh infra start' 를 실행하세요."
    return 1
  fi
}

service_list() {
  local target="${1:-all}"
  case "$target" in
    all) printf '%s\n' api web upbit-gateway realtime-collection-worker backfill-collection-worker candle-aggregation-worker ;;
    api | web | upbit-gateway | realtime-collection-worker | backfill-collection-worker | candle-aggregation-worker) printf '%s\n' "$target" ;;
    *) print_error "알 수 없는 app 대상: $target"; return 2 ;;
  esac
}

infra_list() {
  local target="${1:-all}"
  case "$target" in
    all | postgres) printf '%s\n' postgres ;;
    *) print_error "알 수 없는 infra 대상: $target"; return 2 ;;
  esac
}

pid_file_for() {
  printf '%s/%s.pid\n' "$PID_DIR" "$1"
}

log_file_for() {
  printf '%s/%s.log\n' "$LOG_DIR" "$1"
}

port_for() {
  case "$1" in
    api) printf '%s\n' "$API_PORT" ;;
    web) printf '%s\n' "$WEB_PORT" ;;
    upbit-gateway) printf '%s\n' "$UPBIT_GATEWAY_PORT" ;;
    *) return 1 ;;
  esac
}

pid_from_file() {
  local unit="$1"
  local file
  file="$(pid_file_for "$unit")"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(cat "$file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      printf '%s\n' "$pid"
      return 0
    fi
  fi
  return 1
}

pid_from_port() {
  local unit="$1"
  local port
  port="$(port_for "$unit" 2>/dev/null || true)"
  [[ -n "$port" ]] || return 1

  local pid command
  for pid in $(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    case "$unit" in
      api)
        [[ "$command" == *"goodmoneying_api.main:app"* || "$command" == *"uvicorn"* ]] || continue
        ;;
      web)
        [[ "$command" == *"vite"* || "$command" == *"apps/web"* ]] || continue
        ;;
      upbit-gateway)
        [[ "$command" == *"goodmoneying_upbit_gateway.main:app"* || "$command" == *"uvicorn"* ]] || continue
        ;;
    esac
    printf '%s\n' "$pid"
    return 0
  done
  return 1
}

pid_from_process_table() {
  local unit="$1"
  local pattern
  case "$unit" in
    realtime-collection-worker) pattern="goodmoneying_worker.realtime_collection_worker" ;;
    backfill-collection-worker) pattern="goodmoneying_worker.backfill_collection_worker" ;;
    candle-aggregation-worker) pattern="goodmoneying_worker.aggregation_collection_worker" ;;
    *) return 1 ;;
  esac

  ps -eo pid=,command= | awk -v pattern="$pattern" '
    index($0, pattern) && index($0, "awk -v pattern") == 0 {
      print $1
      found = 1
      exit 0
    }
    END {
      if (!found) {
        exit 1
      }
    }
  '
}

pid_for_unit() {
  local unit="$1"
  pid_from_file "$unit" || pid_from_port "$unit" || pid_from_process_table "$unit"
}

child_pids_for() {
  local parent_pid="$1"
  ps -axo ppid=,pid= | awk -v parent_pid="$parent_pid" '$1 == parent_pid { print $2 }'
}

kill_process_tree() {
  local pid="$1"
  local signal="${2:-TERM}"
  local child
  for child in $(child_pids_for "$pid"); do
    kill_process_tree "$child" "$signal"
  done
  kill "-$signal" "$pid" >/dev/null 2>&1 || true
}

start_background() {
  local unit="$1"
  shift
  ensure_runtime_dirs
  local log_file
  local pid_file
  log_file="$(log_file_for "$unit")"
  pid_file="$(pid_file_for "$unit")"

  if pid_for_unit "$unit" >/dev/null 2>&1; then
    printf 'app %s 는 이미 실행 중입니다. pid=%s\n' "$unit" "$(pid_for_unit "$unit")"
    return 0
  fi

  (
    cd "$ROOT_DIR"
    "$PYTHON_BIN" scripts/dev-start-background.py "$pid_file" "$log_file" "$@"
  )

  sleep 1
  if ! pid_from_file "$unit" >/dev/null 2>&1; then
    print_error "app ${unit} 시작에 실패했습니다. 로그: $log_file"
    tail -80 "$log_file" 2>/dev/null || true
    return 1
  fi
  printf 'app %s 시작 완료. pid=%s log=%s\n' "$unit" "$(pid_from_file "$unit")" "$log_file"
}

start_api() {
  require_local_database_port
  start_background api \
    env PYTHONPATH=apps/api:apps/worker:packages/shared \
      GOODMONEYING_DATABASE_URL="$DATABASE_URL" \
      GOODMONEYING_OPERATOR_TOKEN="$OPERATOR_TOKEN" \
      TZ="$APP_TIMEZONE" \
      PGTZ="$APP_TIMEZONE" \
      "$PYTHON_BIN" -m uvicorn goodmoneying_api.main:app --host "$API_HOST" --port "$API_PORT"
}

start_web() {
  start_background web \
    env VITE_API_BASE_URL="http://${API_HOST}:${API_PORT}" \
      VITE_OPERATOR_TOKEN="$OPERATOR_TOKEN" \
      GOODMONEYING_WEB_HOST="$WEB_HOST" \
      GOODMONEYING_WEB_PORT="$WEB_PORT" \
      TZ="$APP_TIMEZONE" \
      node scripts/dev-vite-server.mjs
}

wait_http_ready() {
  local unit="$1"
  local endpoint="$2"
  for _ in {1..20}; do
    if "$PYTHON_BIN" -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=1).read()' "$endpoint" >/dev/null 2>&1; then
      printf 'app %s 준비 완료. health=%s\n' "$unit" "$endpoint"
      return 0
    fi
    sleep 0.25
  done
  print_error "app ${unit} 준비 확인에 실패했습니다. endpoint=${endpoint}"
  stop_app_unit "$unit"
  return 1
}

start_upbit_gateway() {
  start_background upbit-gateway \
    env PYTHONPATH=apps/upbit_gateway \
      TZ="$APP_TIMEZONE" \
      "$PYTHON_BIN" -m uvicorn goodmoneying_upbit_gateway.main:app \
      --host "$UPBIT_GATEWAY_HOST" --port "$UPBIT_GATEWAY_PORT"
  wait_http_ready upbit-gateway "http://${UPBIT_GATEWAY_HOST}:${UPBIT_GATEWAY_PORT}/health"
}

start_realtime_collection_worker() {
  require_local_database_port
  start_background realtime-collection-worker \
    env PYTHONPATH=apps/api:apps/worker:packages/shared \
      GOODMONEYING_DATABASE_URL="$DATABASE_URL" \
      GOODMONEYING_OPERATOR_TOKEN="$OPERATOR_TOKEN" \
      GOODMONEYING_LIVE_UPBIT="${GOODMONEYING_LIVE_UPBIT:-1}" \
      GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS="$REALTIME_COLLECTION_INTERVAL_SECONDS" \
      GOODMONEYING_LOG_LEVEL="$LOG_LEVEL" \
      GOODMONEYING_PYTHON_BIN="$PYTHON_BIN" \
      TZ="$APP_TIMEZONE" \
      PGTZ="$APP_TIMEZONE" \
      bash -c 'while true; do "$GOODMONEYING_PYTHON_BIN" -m goodmoneying_worker.realtime_collection_worker; sleep "$GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS"; done'
}

start_backfill_collection_worker() {
  require_local_database_port
  start_background backfill-collection-worker \
    env PYTHONPATH=apps/api:apps/worker:packages/shared \
      GOODMONEYING_DATABASE_URL="$DATABASE_URL" \
      GOODMONEYING_OPERATOR_TOKEN="$OPERATOR_TOKEN" \
      GOODMONEYING_LIVE_UPBIT="${GOODMONEYING_LIVE_UPBIT:-1}" \
      GOODMONEYING_BACKFILL_POLL_SECONDS="$BACKFILL_POLL_SECONDS" \
      GOODMONEYING_BACKFILL_BATCH_SIZE="$BACKFILL_BATCH_SIZE" \
      GOODMONEYING_LOG_LEVEL="$LOG_LEVEL" \
      GOODMONEYING_PYTHON_BIN="$PYTHON_BIN" \
      TZ="$APP_TIMEZONE" \
      PGTZ="$APP_TIMEZONE" \
      bash -c '"$GOODMONEYING_PYTHON_BIN" -m goodmoneying_worker.backfill_collection_worker'
}

start_candle_aggregation_worker() {
  require_local_database_port
  start_background candle-aggregation-worker \
    env PYTHONPATH=apps/api:apps/worker:packages/shared \
      GOODMONEYING_DATABASE_URL="$DATABASE_URL" \
      GOODMONEYING_AGGREGATION_POLL_SECONDS="$AGGREGATION_POLL_SECONDS" \
      GOODMONEYING_LOG_LEVEL="$LOG_LEVEL" \
      TZ="$APP_TIMEZONE" \
      PGTZ="$APP_TIMEZONE" \
      "$PYTHON_BIN" -m goodmoneying_worker.aggregation_collection_worker
}

start_app_unit() {
  case "$1" in
    api) start_api ;;
    web) start_web ;;
    upbit-gateway) start_upbit_gateway ;;
    realtime-collection-worker) start_realtime_collection_worker ;;
    backfill-collection-worker) start_backfill_collection_worker ;;
    candle-aggregation-worker) start_candle_aggregation_worker ;;
    *) print_error "알 수 없는 app 대상: $1"; return 2 ;;
  esac
}

stop_app_unit() {
  local unit="$1"
  local pid
  pid="$(pid_for_unit "$unit" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    rm -f "$(pid_file_for "$unit")"
    printf 'app %s 는 이미 중지되어 있습니다.\n' "$unit"
    return 0
  fi

  kill_process_tree "$pid" TERM
  for _ in {1..20}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$(pid_file_for "$unit")"
      printf 'app %s 중지 완료. pid=%s\n' "$unit" "$pid"
      return 0
    fi
    sleep 0.2
  done
  kill_process_tree "$pid" KILL
  rm -f "$(pid_file_for "$unit")"
  printf 'app %s 강제 중지 완료. pid=%s\n' "$unit" "$pid"
}

status_app_unit() {
  local unit="$1"
  local pid
  local endpoint=""
  case "$unit" in
    api) endpoint=" endpoint=http://${API_HOST}:${API_PORT}" ;;
    web) endpoint=" endpoint=http://${WEB_HOST}:${WEB_PORT}/" ;;
    upbit-gateway) endpoint=" endpoint=http://${UPBIT_GATEWAY_HOST}:${UPBIT_GATEWAY_PORT}" ;;
  esac
  pid="$(pid_for_unit "$unit" 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    printf 'app %-7s running pid=%s%s\n' "$unit" "$pid" "$endpoint"
  else
    printf 'app %-7s stopped%s\n' "$unit" "$endpoint"
  fi
}

infra_start() {
  local target="${1:-all}"
  local unit
  for unit in $(infra_list "$target"); do
    podman_compose up -d "$unit"
  done
}

infra_stop() {
  local target="${1:-all}"
  local unit
  for unit in $(infra_list "$target"); do
    podman_compose stop "$unit"
  done
}

infra_status() {
  printf 'infra\n'
  if ! command -v podman >/dev/null 2>&1; then
    printf 'infra postgres unavailable podman-not-found\n'
    return 0
  fi
  local target="${1:-all}"
  local unit
  for unit in $(infra_list "$target"); do
    local name="goodmoneying-${unit}-1"
    local line
    line="$(podman ps -a --filter "name=${name}" --format '{{.Names}} {{.Status}} {{.Ports}}' 2>/dev/null || true)"
    if [[ -n "$line" ]]; then
      printf 'infra %s %s\n' "$unit" "$line"
    else
      printf 'infra %s not-created\n' "$unit"
    fi
  done
}

app_status() {
  local target="${1:-all}"
  printf 'app\n'
  local unit
  for unit in $(service_list "$target"); do
    status_app_unit "$unit"
  done
}

app_start() {
  local target="${1:-all}"
  local unit
  service_list "$target" >/dev/null
  if [[ "$target" != "upbit-gateway" ]]; then
    run_dbmate --no-dump-schema migrate
  fi
  for unit in $(service_list "$target"); do
    start_app_unit "$unit"
  done
}

app_stop() {
  local target="${1:-all}"
  local unit
  for unit in $(service_list "$target"); do
    stop_app_unit "$unit"
  done
}

show_logs() {
  local unit="${1:-}"
  if [[ -z "$unit" ]]; then
    print_error "logs 대상이 필요합니다: api, web, upbit-gateway, realtime-collection-worker, backfill-collection-worker, candle-aggregation-worker"
    usage
    return 2
  fi
  case "$unit" in
    api | web | upbit-gateway | realtime-collection-worker | backfill-collection-worker | candle-aggregation-worker) ;;
    *) print_error "알 수 없는 logs 대상: $unit"; return 2 ;;
  esac
  tail -n 120 -f "$(log_file_for "$unit")"
}

main() {
  if [[ $# -eq 0 ]]; then
    usage
    return 0
  fi

  local group="$1"
  shift
  case "$group" in
    status)
      infra_status
      app_status
      ;;
    infra)
      local action="${1:-status}"
      local target="${2:-all}"
      case "$action" in
        start) infra_start "$target" ;;
        stop) infra_stop "$target" ;;
        restart) infra_stop "$target"; infra_start "$target" ;;
        status) infra_status "$target" ;;
        *) print_error "알 수 없는 infra 명령: $action"; usage; return 2 ;;
      esac
      ;;
    db)
      db_command "$@"
      ;;
    app)
      local action="${1:-status}"
      local target="${2:-all}"
      case "$action" in
        start) app_start "$target" ;;
        stop) app_stop "$target" ;;
        restart) app_stop "$target"; app_start "$target" ;;
        status) app_status "$target" ;;
        *) print_error "알 수 없는 app 명령: $action"; usage; return 2 ;;
      esac
      ;;
    logs)
      show_logs "${1:-}"
      ;;
    help | -h | --help)
      usage
      ;;
    *)
      print_error "알 수 없는 명령: $group"
      usage
      return 2
      ;;
  esac
}

main "$@"
