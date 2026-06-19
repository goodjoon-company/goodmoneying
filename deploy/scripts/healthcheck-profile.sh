#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="${1:-}"
DRY_RUN="${GOODMONEYING_DEPLOY_DRY_RUN:-0}"

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
source "$PROFILE_DIR/profile.env"
source "$PROFILE_DIR/hosts.env"

curl_args=(-fsS --connect-timeout 5 --max-time 10)
ssh_args=(-o BatchMode=yes -o ConnectTimeout=10)
api_health_url="$GOODMONEYING_API_INTERNAL_URL/health"
web_health_url="$GOODMONEYING_WEB_INTERNAL_URL/"
postgres_check='pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
worker_check_template="{{.State.Running}}"

commands=(
  "curl ${curl_args[*]} $api_health_url"
  "curl ${curl_args[*]} $web_health_url"
  "ssh ${ssh_args[*]} $GOODMONEYING_INFRA_HOST docker exec goodmoneying-postgres sh -c '$postgres_check'"
  "ssh ${ssh_args[*]} $GOODMONEYING_APP_HOST docker inspect -f '$worker_check_template' goodmoneying-worker"
)

if [[ "$DRY_RUN" == "1" ]]; then
  printf '%s\n' "${commands[@]}"
  exit 0
fi

curl "${curl_args[@]}" "$api_health_url" >/dev/null
curl "${curl_args[@]}" "$web_health_url" >/dev/null
ssh "${ssh_args[@]}" \
  "$GOODMONEYING_INFRA_HOST" \
  docker exec goodmoneying-postgres sh -c "$postgres_check"
worker_running="$(
  ssh "${ssh_args[@]}" \
    "$GOODMONEYING_APP_HOST" \
    docker inspect -f "$worker_check_template" goodmoneying-worker
)"
if [[ "$worker_running" != "true" ]]; then
  fail "worker 컨테이너가 실행 중이 아닙니다."
fi
