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
  fail "사용법: deploy/scripts/start-profile.sh prod-home"
fi

if [[ "$PROFILE" != "prod-home" ]]; then
  fail "지원하지 않는 배포 프로필입니다: $PROFILE"
fi

PROFILE_DIR="$ROOT_DIR/deploy/profiles/$PROFILE"
RUNNER_DIR="$PROFILE_DIR/runner"
source "$RUNNER_DIR/profile.env"
source "$RUNNER_DIR/hosts.env"

print_compose_command() {
  local host="$1"
  local base_dir="$2"
  local compose_file="$3"
  local docker_config="$4"
  local compose_env="$base_dir/deploy.compose.env"

  printf 'ssh %s "cd '\''%s'\'' && DOCKER_CONFIG='\''%s'\'' PATH=%s docker compose --env-file '\''%s'\'' -f '\''%s'\'' up -d"\n' \
    "$host" \
    "$base_dir" \
    "$docker_config" \
    "$REMOTE_DOCKER_PATH" \
    "$compose_env" \
    "$compose_file"
}

run_compose_command() {
  local host="$1"
  local base_dir="$2"
  local compose_file="$3"
  local docker_config="$4"
  local compose_env="$base_dir/deploy.compose.env"

  ssh "$host" "cd '$base_dir' && DOCKER_CONFIG='$docker_config' PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:\$PATH docker compose --env-file '$compose_env' -f '$compose_file' up -d"
}

if [[ "$DRY_RUN" == "1" ]]; then
  print_compose_command "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_BASE_DIR" "$GOODMONEYING_INFRA_COMPOSE" "$GOODMONEYING_INFRA_DOCKER_CONFIG"
  print_compose_command "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_BASE_DIR" "$GOODMONEYING_APP_COMPOSE" "$GOODMONEYING_APP_DOCKER_CONFIG"
  print_compose_command "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_BASE_DIR" "$GOODMONEYING_WEB_COMPOSE" "$GOODMONEYING_WEB_DOCKER_CONFIG"
  exit 0
fi

run_compose_command "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_BASE_DIR" "$GOODMONEYING_INFRA_COMPOSE" "$GOODMONEYING_INFRA_DOCKER_CONFIG"
run_compose_command "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_BASE_DIR" "$GOODMONEYING_APP_COMPOSE" "$GOODMONEYING_APP_DOCKER_CONFIG"
run_compose_command "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_BASE_DIR" "$GOODMONEYING_WEB_COMPOSE" "$GOODMONEYING_WEB_DOCKER_CONFIG"
