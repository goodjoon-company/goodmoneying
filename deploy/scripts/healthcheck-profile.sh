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

commands=(
  "curl -fsS $GOODMONEYING_API_INTERNAL_URL/health"
  "curl -fsS $GOODMONEYING_WEB_INTERNAL_URL/"
  "ssh $GOODMONEYING_INFRA_HOST docker exec goodmoneying-postgres pg_isready"
  "ssh $GOODMONEYING_APP_HOST docker inspect -f '{{.State.Running}}' goodmoneying-worker"
)

if [[ "$DRY_RUN" == "1" ]]; then
  printf '%s\n' "${commands[@]}"
  exit 0
fi

curl -fsS "$GOODMONEYING_API_INTERNAL_URL/health" >/dev/null
curl -fsS "$GOODMONEYING_WEB_INTERNAL_URL/" >/dev/null
ssh "$GOODMONEYING_INFRA_HOST" "docker exec goodmoneying-postgres pg_isready"
worker_running="$(
  ssh "$GOODMONEYING_APP_HOST" \
    "docker inspect -f '{{.State.Running}}' goodmoneying-worker"
)"
if [[ "$worker_running" != "true" ]]; then
  fail "worker 컨테이너가 실행 중이 아닙니다."
fi
