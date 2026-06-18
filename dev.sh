#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${GOODMONEYING_DEV_DIR:-"$ROOT_DIR/.dev"}"
PID_DIR="$RUNTIME_DIR/pids"
LOG_DIR="$RUNTIME_DIR/logs"

API_HOST="${GOODMONEYING_API_HOST:-127.0.0.1}"
API_PORT="${GOODMONEYING_API_PORT:-8000}"
WEB_HOST="${GOODMONEYING_WEB_HOST:-127.0.0.1}"
WEB_PORT="${GOODMONEYING_WEB_PORT:-5173}"
POSTGRES_PORT="${GOODMONEYING_POSTGRES_PORT:-5432}"
OPERATOR_TOKEN="${GOODMONEYING_OPERATOR_TOKEN:-local-dev-token}"
DATABASE_URL="${GOODMONEYING_DATABASE_URL:-postgresql://goodmoneying:goodmoneying@127.0.0.1:${POSTGRES_PORT}/goodmoneying}"
WORKER_INTERVAL_SECONDS="${GOODMONEYING_WORKER_INTERVAL_SECONDS:-60}"

usage() {
  cat <<'USAGE'
사용법:
  ./dev.sh
  ./dev.sh status

  ./dev.sh infra start [postgres|all]
  ./dev.sh infra stop [postgres|all]
  ./dev.sh infra restart [postgres|all]
  ./dev.sh infra status [postgres|all]

  ./dev.sh app start [api|web|worker|all]
  ./dev.sh app stop [api|web|worker|all]
  ./dev.sh app restart [api|web|worker|all]
  ./dev.sh app status [api|web|worker|all]

  ./dev.sh logs [api|web|worker]

설명:
  infra 는 Podman Compose 로 PostgreSQL 을 관리한다.
  app 은 로컬 개발 프로세스로 실행한다. API 는 기본적으로 PostgreSQL 을 바라본다.

기본 endpoint:
  Web: http://127.0.0.1:5173/
  API: http://127.0.0.1:8000
  Health: http://127.0.0.1:8000/health

주요 환경변수:
  GOODMONEYING_DATABASE_URL
  GOODMONEYING_OPERATOR_TOKEN
  GOODMONEYING_API_PORT
  GOODMONEYING_WEB_PORT
  GOODMONEYING_WORKER_INTERVAL_SECONDS
USAGE
}

ensure_runtime_dirs() {
  mkdir -p "$PID_DIR" "$LOG_DIR"
}

print_error() {
  printf '오류: %s\n' "$*" >&2
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

require_infra_port() {
  if ! lsof -nP -iTCP:"$POSTGRES_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    print_error "PostgreSQL 포트 ${POSTGRES_PORT} 이 열려 있지 않습니다. 먼저 './dev.sh infra start' 를 실행하세요."
    return 1
  fi
}

service_list() {
  local target="${1:-all}"
  case "$target" in
    all) printf '%s\n' api web worker ;;
    api | web | worker) printf '%s\n' "$target" ;;
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
    worker) pattern="goodmoneying_worker.main" ;;
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
    nohup "$@" >"$log_file" 2>&1 &
    printf '%s\n' "$!" >"$pid_file"
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
  require_infra_port
  start_background api \
    env PYTHONPATH=apps/api:apps/worker:packages/shared \
      GOODMONEYING_DATABASE_URL="$DATABASE_URL" \
      GOODMONEYING_OPERATOR_TOKEN="$OPERATOR_TOKEN" \
      uv run uvicorn goodmoneying_api.main:app --host "$API_HOST" --port "$API_PORT"
}

start_web() {
  start_background web \
    env VITE_API_BASE_URL="http://${API_HOST}:${API_PORT}" \
      npm --workspace apps/web run dev -- --host "$WEB_HOST" --port "$WEB_PORT"
}

start_worker() {
  require_infra_port
  start_background worker \
    env PYTHONPATH=apps/api:apps/worker:packages/shared \
      GOODMONEYING_DATABASE_URL="$DATABASE_URL" \
      GOODMONEYING_OPERATOR_TOKEN="$OPERATOR_TOKEN" \
      GOODMONEYING_LIVE_UPBIT="${GOODMONEYING_LIVE_UPBIT:-1}" \
      GOODMONEYING_WORKER_INTERVAL_SECONDS="$WORKER_INTERVAL_SECONDS" \
      bash -c 'while true; do uv run python -m goodmoneying_worker.main; sleep "$GOODMONEYING_WORKER_INTERVAL_SECONDS"; done'
}

start_app_unit() {
  case "$1" in
    api) start_api ;;
    web) start_web ;;
    worker) start_worker ;;
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
  pid="$(pid_for_unit "$unit" 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    printf 'app %-7s running pid=%s' "$unit" "$pid"
    case "$unit" in
      api) printf ' endpoint=http://%s:%s' "$API_HOST" "$API_PORT" ;;
      web) printf ' endpoint=http://%s:%s/' "$WEB_HOST" "$WEB_PORT" ;;
    esac
    printf '\n'
  else
    printf 'app %-7s stopped\n' "$unit"
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
    print_error "logs 대상이 필요합니다: api, web, worker"
    usage
    return 2
  fi
  case "$unit" in
    api | web | worker) ;;
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
