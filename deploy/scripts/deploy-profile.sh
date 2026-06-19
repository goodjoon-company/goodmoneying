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
  local base_dir="$2"
  local compose_file="$3"
  shift 3
  local volume_dirs=("$@")
  local remote_compose_env="$base_dir/deploy.hosts.env"
  printf 'ssh %s "mkdir -p '\''%s'\''"\n' \
    "$host" \
    "$base_dir"
  for volume_dir in "${volume_dirs[@]}"; do
    printf 'ssh %s "mkdir -p '\''%s'\''"\n' \
      "$host" \
      "$volume_dir"
  done
  printf 'scp %s/hosts.env %s:%s\n' \
    "$PROFILE_DIR" \
    "$host" \
    "$remote_compose_env"
  printf 'scp %s/%s %s:%s/%s\n' \
    "$PROFILE_DIR" \
    "$compose_file" \
    "$host" \
    "$base_dir" \
    "$compose_file"
  printf 'ssh %s "cd '\''%s'\'' && GOODMONEYING_IMAGE_TAG='\''%s'\'' docker compose --env-file '\''%s'\'' -f '\''%s'\'' pull"\n' \
    "$host" \
    "$base_dir" \
    "$IMAGE_TAG" \
    "$remote_compose_env" \
    "$compose_file"
  printf 'ssh %s "cd '\''%s'\'' && GOODMONEYING_IMAGE_TAG='\''%s'\'' docker compose --env-file '\''%s'\'' -f '\''%s'\'' up -d"\n' \
    "$host" \
    "$base_dir" \
    "$IMAGE_TAG" \
    "$remote_compose_env" \
    "$compose_file"
}

run_remote_compose() {
  local host="$1"
  local base_dir="$2"
  local compose_file="$3"
  shift 3
  local volume_dirs=("$@")
  local remote_compose_env="$base_dir/deploy.hosts.env"
  ssh "$host" "mkdir -p '$base_dir'"
  for volume_dir in "${volume_dirs[@]}"; do
    ssh "$host" "mkdir -p '$volume_dir'"
  done
  scp "$PROFILE_DIR/hosts.env" "$host:$remote_compose_env"
  scp "$PROFILE_DIR/$compose_file" "$host:$base_dir/$compose_file"
  ssh "$host" "cd '$base_dir' && GOODMONEYING_IMAGE_TAG='$IMAGE_TAG' docker compose --env-file '$remote_compose_env' -f '$compose_file' pull"
  ssh "$host" "cd '$base_dir' && GOODMONEYING_IMAGE_TAG='$IMAGE_TAG' docker compose --env-file '$remote_compose_env' -f '$compose_file' up -d"
}

if [[ "$DRY_RUN" == "1" ]]; then
  printf 'profile=%s\n' "$GOODMONEYING_DEPLOY_PROFILE"
  printf 'tag=%s\n' "$IMAGE_TAG"
  printf 'infra host=%s compose=%s\n' "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_COMPOSE"
  printf 'app host=%s compose=%s\n' "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_COMPOSE"
  printf 'web host=%s compose=%s\n' "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_COMPOSE"
  print_remote_compose "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_BASE_DIR" "$GOODMONEYING_INFRA_COMPOSE" \
    "$GOODMONEYING_INFRA_POSTGRES_DATA_DIR" \
    "$GOODMONEYING_INFRA_CONFIG_DIR"
  print_remote_compose "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_BASE_DIR" "$GOODMONEYING_APP_COMPOSE" \
    "$GOODMONEYING_APP_API_DATA_DIR" \
    "$GOODMONEYING_APP_WORKER_DATA_DIR" \
    "$GOODMONEYING_APP_CONFIG_DIR"
  print_remote_compose "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_BASE_DIR" "$GOODMONEYING_WEB_COMPOSE" \
    "$GOODMONEYING_WEB_NGINX_CACHE_DIR" \
    "$GOODMONEYING_WEB_CONFIG_DIR"
  exit 0
fi

printf 'prod-home 배포를 시작합니다. tag=%s\n' "$IMAGE_TAG"
run_remote_compose "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_BASE_DIR" "$GOODMONEYING_INFRA_COMPOSE" \
  "$GOODMONEYING_INFRA_POSTGRES_DATA_DIR" \
  "$GOODMONEYING_INFRA_CONFIG_DIR"
run_remote_compose "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_BASE_DIR" "$GOODMONEYING_APP_COMPOSE" \
  "$GOODMONEYING_APP_API_DATA_DIR" \
  "$GOODMONEYING_APP_WORKER_DATA_DIR" \
  "$GOODMONEYING_APP_CONFIG_DIR"
run_remote_compose "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_BASE_DIR" "$GOODMONEYING_WEB_COMPOSE" \
  "$GOODMONEYING_WEB_NGINX_CACHE_DIR" \
  "$GOODMONEYING_WEB_CONFIG_DIR"
