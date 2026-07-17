#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="${1:-}"
DRY_RUN="${GOODMONEYING_DEPLOY_DRY_RUN:-0}"
REMOTE_DOCKER_PATH="/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:\$PATH"

fail() {
  printf '오류: %s\n' "$*" >&2
  exit 1
}

if [[ -z "$PROFILE" ]]; then
  fail "사용법: deploy/scripts/healthcheck-profile.sh prod-home"
fi

if [[ "$PROFILE" != "prod-home" ]]; then
  fail "지원하지 않는 배포 프로필입니다: $PROFILE"
fi

PROFILE_DIR="$ROOT_DIR/deploy/profiles/$PROFILE"
RUNNER_DIR="$PROFILE_DIR/runner"
source "$RUNNER_DIR/profile.env"
source "$RUNNER_DIR/hosts.env"

curl_args=(-fsS --connect-timeout 5 --max-time 10)
ssh_args=(-o BatchMode=yes -o ConnectTimeout=10)
retry_attempts="${GOODMONEYING_HEALTHCHECK_RETRIES:-30}"
retry_interval_seconds="${GOODMONEYING_HEALTHCHECK_RETRY_INTERVAL_SECONDS:-2}"
api_health_url="$GOODMONEYING_API_INTERNAL_URL/health"
upbit_gateway_health_url="$GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL/health"
web_health_url="$GOODMONEYING_WEB_INTERNAL_URL/"
postgres_check='pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
container_running_check_template="{{.State.Running}}"
postgres_remote_command="PATH=$REMOTE_DOCKER_PATH docker exec goodmoneying-postgres sh -c '$postgres_check'"
realtime_worker_remote_command="PATH=$REMOTE_DOCKER_PATH docker inspect -f '$container_running_check_template' goodmoneying-realtime-collection-worker"
backfill_worker_remote_command="PATH=$REMOTE_DOCKER_PATH docker inspect -f '$container_running_check_template' goodmoneying-backfill-collection-worker"
market_sync_worker_remote_command="PATH=$REMOTE_DOCKER_PATH docker inspect -f '$container_running_check_template' goodmoneying-market-sync-worker"
candle_aggregation_worker_remote_command="PATH=$REMOTE_DOCKER_PATH docker inspect -f '$container_running_check_template' goodmoneying-candle-aggregation-worker"

commands=(
  "retry $retry_attempts ${retry_interval_seconds}s curl ${curl_args[*]} $api_health_url"
  "retry $retry_attempts ${retry_interval_seconds}s curl ${curl_args[*]} $upbit_gateway_health_url"
  "retry $retry_attempts ${retry_interval_seconds}s curl ${curl_args[*]} $web_health_url"
  "ssh ${ssh_args[*]} $GOODMONEYING_INFRA_HOST $postgres_remote_command"
  "ssh ${ssh_args[*]} $GOODMONEYING_APP_HOST $realtime_worker_remote_command"
  "ssh ${ssh_args[*]} $GOODMONEYING_APP_HOST $backfill_worker_remote_command"
  "ssh ${ssh_args[*]} $GOODMONEYING_APP_HOST $market_sync_worker_remote_command"
  "ssh ${ssh_args[*]} $GOODMONEYING_APP_HOST $candle_aggregation_worker_remote_command"
)

if [[ "$DRY_RUN" == "1" ]]; then
  printf '%s\n' "${commands[@]}"
  exit 0
fi

retry_command() {
  local label="$1"
  shift
  local attempt=1
  local last_status=0
  while (( attempt <= retry_attempts )); do
    if "$@"; then
      return 0
    fi
    last_status=$?
    printf '대기 중: %s 실패(%d/%d). %s초 뒤 재시도합니다.\n' \
      "$label" \
      "$attempt" \
      "$retry_attempts" \
      "$retry_interval_seconds" >&2
    sleep "$retry_interval_seconds"
    attempt=$((attempt + 1))
  done
  printf '오류: %s 확인 실패. 마지막 exit code=%d\n' "$label" "$last_status" >&2
  return "$last_status"
}

retry_command "API healthcheck" curl "${curl_args[@]}" "$api_health_url" >/dev/null
retry_command "Upbit gateway healthcheck" curl "${curl_args[@]}" "$upbit_gateway_health_url" >/dev/null
retry_command "Web healthcheck" curl "${curl_args[@]}" "$web_health_url" >/dev/null
ssh "${ssh_args[@]}" \
  "$GOODMONEYING_INFRA_HOST" \
  "$postgres_remote_command"
realtime_worker_running="$(
  ssh "${ssh_args[@]}" \
    "$GOODMONEYING_APP_HOST" \
    "$realtime_worker_remote_command"
)"
realtime_worker_running="${realtime_worker_running//$'\r'/}"
realtime_worker_running="${realtime_worker_running//$'\n'/}"
if [[ "$realtime_worker_running" != "true" ]]; then
  fail "realtime-collection-worker 컨테이너가 실행 중이 아닙니다."
fi

backfill_worker_running="$(
  ssh "${ssh_args[@]}" \
    "$GOODMONEYING_APP_HOST" \
    "$backfill_worker_remote_command"
)"
backfill_worker_running="${backfill_worker_running//$'\r'/}"
backfill_worker_running="${backfill_worker_running//$'\n'/}"
if [[ "$backfill_worker_running" != "true" ]]; then
  fail "backfill-collection-worker 컨테이너가 실행 중이 아닙니다."
fi

for worker_name in market-sync-worker candle-aggregation-worker; do
  worker_running="$(ssh "${ssh_args[@]}" "$GOODMONEYING_APP_HOST" \
    "PATH=$REMOTE_DOCKER_PATH docker inspect -f '$container_running_check_template' goodmoneying-$worker_name")"
  worker_running="${worker_running//$'\r'/}"
  worker_running="${worker_running//$'\n'/}"
  if [[ "$worker_running" != "true" ]]; then
    fail "$worker_name 컨테이너가 실행 중이 아닙니다."
  fi
done
