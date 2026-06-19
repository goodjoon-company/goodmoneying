#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="${1:-}"
IMAGE_TAG="${2:-}"
DRY_RUN="${GOODMONEYING_DEPLOY_DRY_RUN:-0}"

fail() {
  printf '오류: %s\n' "$*" >&2
  exit 1
}

if [[ -z "$PROFILE" || -z "$IMAGE_TAG" ]]; then
  fail "사용법: deploy/scripts/deploy-profile.sh prod-home release-{short-sha}"
fi

if [[ "$PROFILE" != "prod-home" ]]; then
  fail "지원하지 않는 배포 프로필입니다: $PROFILE"
fi

if [[ ! "$IMAGE_TAG" =~ ^release-[0-9a-f]{7,40}$ ]]; then
  fail "잘못된 이미지 태그입니다: $IMAGE_TAG"
fi

PROFILE_DIR="$ROOT_DIR/deploy/profiles/$PROFILE"
source "$PROFILE_DIR/profile.env"
source "$PROFILE_DIR/hosts.env"

print_remote_compose() {
  local host="$1"
  local compose_file="$2"
  local env_file="$3"
  printf 'ssh %s "mkdir -p '\''%s'\''"\n' \
    "$host" \
    "$GOODMONEYING_REMOTE_BASE_DIR"
  printf 'scp %s/%s %s:%s/%s\n' \
    "$PROFILE_DIR" \
    "$compose_file" \
    "$host" \
    "$GOODMONEYING_REMOTE_BASE_DIR" \
    "$compose_file"
  printf 'ssh %s "cd '\''%s'\'' && GOODMONEYING_IMAGE_TAG='\''%s'\'' docker compose --env-file '\''%s'\'' -f '\''%s'\'' pull"\n' \
    "$host" \
    "$GOODMONEYING_REMOTE_BASE_DIR" \
    "$IMAGE_TAG" \
    "$env_file" \
    "$compose_file"
  printf 'ssh %s "cd '\''%s'\'' && GOODMONEYING_IMAGE_TAG='\''%s'\'' docker compose --env-file '\''%s'\'' -f '\''%s'\'' up -d"\n' \
    "$host" \
    "$GOODMONEYING_REMOTE_BASE_DIR" \
    "$IMAGE_TAG" \
    "$env_file" \
    "$compose_file"
}

run_remote_compose() {
  local host="$1"
  local compose_file="$2"
  local env_file="$3"
  ssh "$host" "mkdir -p '$GOODMONEYING_REMOTE_BASE_DIR'"
  scp "$PROFILE_DIR/$compose_file" "$host:$GOODMONEYING_REMOTE_BASE_DIR/$compose_file"
  ssh "$host" "cd '$GOODMONEYING_REMOTE_BASE_DIR' && GOODMONEYING_IMAGE_TAG='$IMAGE_TAG' docker compose --env-file '$env_file' -f '$compose_file' pull"
  ssh "$host" "cd '$GOODMONEYING_REMOTE_BASE_DIR' && GOODMONEYING_IMAGE_TAG='$IMAGE_TAG' docker compose --env-file '$env_file' -f '$compose_file' up -d"
}

if [[ "$DRY_RUN" == "1" ]]; then
  printf 'profile=%s\n' "$GOODMONEYING_DEPLOY_PROFILE"
  printf 'tag=%s\n' "$IMAGE_TAG"
  printf 'infra host=%s compose=%s\n' "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_COMPOSE"
  printf 'app host=%s compose=%s\n' "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_COMPOSE"
  printf 'web host=%s compose=%s\n' "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_COMPOSE"
  print_remote_compose "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_COMPOSE" "$GOODMONEYING_REMOTE_BASE_DIR/env/infra.env"
  print_remote_compose "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_COMPOSE" "$GOODMONEYING_REMOTE_BASE_DIR/env/app.env"
  print_remote_compose "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_COMPOSE" "$GOODMONEYING_REMOTE_BASE_DIR/env/web.env"
  exit 0
fi

printf 'prod-home 배포를 시작합니다. tag=%s\n' "$IMAGE_TAG"
run_remote_compose "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_COMPOSE" "$GOODMONEYING_REMOTE_BASE_DIR/env/infra.env"
run_remote_compose "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_COMPOSE" "$GOODMONEYING_REMOTE_BASE_DIR/env/app.env"
run_remote_compose "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_COMPOSE" "$GOODMONEYING_REMOTE_BASE_DIR/env/web.env"
