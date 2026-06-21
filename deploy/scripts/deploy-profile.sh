#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="${1:-}"
IMAGE_TAG="${2:-}"
DRY_RUN="${GOODMONEYING_DEPLOY_DRY_RUN:-0}"
REMOTE_DOCKER_PATH="/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:\$PATH"

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
RUNNER_DIR="$PROFILE_DIR/runner"
TARGET_DIR="$PROFILE_DIR/target"
source "$RUNNER_DIR/profile.env"
source "$RUNNER_DIR/hosts.env"

print_remote_compose() {
  local host="$1"
  local base_dir="$2"
  local target_role="$3"
  local source_compose_file="$4"
  local remote_compose_file="$5"
  local docker_config="$6"
  shift 6
  local volume_dirs=("$@")
  local remote_hosts_env="$base_dir/deploy.hosts.env"
  local remote_compose_env="$base_dir/deploy.compose.env"
  local script_path
  local script_name
  printf 'ssh %s "mkdir -p '\''%s'\''"\n' \
    "$host" \
    "$base_dir"
  for volume_dir in "${volume_dirs[@]}"; do
    printf 'ssh %s "mkdir -p '\''%s'\''"\n' \
      "$host" \
      "$volume_dir"
  done
  printf 'scp %s/hosts.env %s:%s\n' \
    "$RUNNER_DIR" \
    "$host" \
    "$remote_hosts_env"
  printf 'ssh %s "cp '\''%s'\'' '\''%s'\''"\n' \
    "$host" \
    "$remote_hosts_env" \
    "$remote_compose_env"
  printf 'ssh %s "mkdir -p '\''%s/cli-plugins'\'' && if [ -x /Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose ]; then rm -f '\''%s/cli-plugins/docker-compose'\'' && ln -s /Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose '\''%s/cli-plugins/docker-compose'\''; fi"\n' \
    "$host" \
    "$docker_config" \
    "$docker_config" \
    "$docker_config"
  printf 'ssh %s "printf '\''GOODMONEYING_IMAGE_TAG=%%s\\n'\'' '\''%s'\'' >> '\''%s'\''"\n' \
    "$host" \
    "$IMAGE_TAG" \
    "$remote_compose_env"
  printf 'ssh %s "printf '\''GOODMONEYING_DOCKER_CONFIG=%%s\\n'\'' '\''%s'\'' >> '\''%s'\''"\n' \
    "$host" \
    "$docker_config" \
    "$remote_compose_env"
  printf 'scp %s/%s %s:%s/%s\n' \
    "$TARGET_DIR" \
    "$source_compose_file" \
    "$host" \
    "$base_dir" \
    "$remote_compose_file"
  for script_path in "$TARGET_DIR/$target_role"/*.sh; do
    script_name="$(basename "$script_path")"
    printf 'scp %s %s:%s/%s\n' \
      "$script_path" \
      "$host" \
      "$base_dir" \
      "$script_name"
    printf 'ssh %s "chmod +x '\''%s/%s'\''"\n' \
      "$host" \
      "$base_dir" \
      "$script_name"
  done
  printf 'ssh %s "cd '\''%s'\'' && DOCKER_CONFIG='\''%s'\'' PATH=%s docker compose --env-file '\''%s'\'' -f '\''%s'\'' pull"\n' \
    "$host" \
    "$base_dir" \
    "$docker_config" \
    "$REMOTE_DOCKER_PATH" \
    "$remote_compose_env" \
    "$remote_compose_file"
  printf 'ssh %s "cd '\''%s'\'' && DOCKER_CONFIG='\''%s'\'' PATH=%s docker compose --env-file '\''%s'\'' -f '\''%s'\'' up -d"\n' \
    "$host" \
    "$base_dir" \
    "$docker_config" \
    "$REMOTE_DOCKER_PATH" \
    "$remote_compose_env" \
    "$remote_compose_file"
}

run_remote_compose() {
  local host="$1"
  local base_dir="$2"
  local target_role="$3"
  local source_compose_file="$4"
  local remote_compose_file="$5"
  local docker_config="$6"
  shift 6
  local volume_dirs=("$@")
  local remote_hosts_env="$base_dir/deploy.hosts.env"
  local remote_compose_env="$base_dir/deploy.compose.env"
  local script_path
  local script_name
  ssh "$host" "mkdir -p '$base_dir'"
  for volume_dir in "${volume_dirs[@]}"; do
    ssh "$host" "mkdir -p '$volume_dir'"
  done
  scp "$RUNNER_DIR/hosts.env" "$host:$remote_hosts_env"
  ssh "$host" "cp '$remote_hosts_env' '$remote_compose_env'"
  ssh "$host" "mkdir -p '$docker_config/cli-plugins' && if [ -x /Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose ]; then rm -f '$docker_config/cli-plugins/docker-compose' && ln -s /Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose '$docker_config/cli-plugins/docker-compose'; fi"
  ssh "$host" "printf 'GOODMONEYING_IMAGE_TAG=%s\n' '$IMAGE_TAG' >> '$remote_compose_env'"
  ssh "$host" "printf 'GOODMONEYING_DOCKER_CONFIG=%s\n' '$docker_config' >> '$remote_compose_env'"
  scp "$TARGET_DIR/$source_compose_file" "$host:$base_dir/$remote_compose_file"
  for script_path in "$TARGET_DIR/$target_role"/*.sh; do
    script_name="$(basename "$script_path")"
    scp "$script_path" "$host:$base_dir/$script_name"
    ssh "$host" "chmod +x '$base_dir/$script_name'"
  done
  ssh "$host" "cd '$base_dir' && DOCKER_CONFIG='$docker_config' PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:\$PATH docker compose --env-file '$remote_compose_env' -f '$remote_compose_file' pull"
  ssh "$host" "cd '$base_dir' && DOCKER_CONFIG='$docker_config' PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:\$PATH docker compose --env-file '$remote_compose_env' -f '$remote_compose_file' up -d"
}

if [[ "$DRY_RUN" == "1" ]]; then
  printf 'profile=%s\n' "$GOODMONEYING_DEPLOY_PROFILE"
  printf 'tag=%s\n' "$IMAGE_TAG"
  printf 'infra host=%s compose=%s\n' "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_COMPOSE"
  printf 'app host=%s compose=%s\n' "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_COMPOSE"
  printf 'web host=%s compose=%s\n' "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_COMPOSE"
  print_remote_compose "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_BASE_DIR" "infra" "infra/compose.yml" "$GOODMONEYING_INFRA_COMPOSE" "$GOODMONEYING_INFRA_DOCKER_CONFIG" \
    "$GOODMONEYING_INFRA_POSTGRES_DATA_DIR" \
    "$GOODMONEYING_INFRA_CONFIG_DIR"
  print_remote_compose "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_BASE_DIR" "app" "app/compose.yml" "$GOODMONEYING_APP_COMPOSE" "$GOODMONEYING_APP_DOCKER_CONFIG" \
    "$GOODMONEYING_APP_API_DATA_DIR" \
    "$GOODMONEYING_APP_REALTIME_COLLECTION_WORKER_DATA_DIR" \
    "$GOODMONEYING_APP_BACKFILL_COLLECTION_WORKER_DATA_DIR" \
    "$GOODMONEYING_APP_CONFIG_DIR"
  print_remote_compose "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_BASE_DIR" "web" "web/compose.yml" "$GOODMONEYING_WEB_COMPOSE" "$GOODMONEYING_WEB_DOCKER_CONFIG" \
    "$GOODMONEYING_WEB_NGINX_CACHE_DIR" \
    "$GOODMONEYING_WEB_CONFIG_DIR"
  exit 0
fi

printf 'prod-home 배포를 시작합니다. tag=%s\n' "$IMAGE_TAG"
run_remote_compose "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_BASE_DIR" "infra" "infra/compose.yml" "$GOODMONEYING_INFRA_COMPOSE" "$GOODMONEYING_INFRA_DOCKER_CONFIG" \
  "$GOODMONEYING_INFRA_POSTGRES_DATA_DIR" \
  "$GOODMONEYING_INFRA_CONFIG_DIR"
run_remote_compose "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_BASE_DIR" "app" "app/compose.yml" "$GOODMONEYING_APP_COMPOSE" "$GOODMONEYING_APP_DOCKER_CONFIG" \
  "$GOODMONEYING_APP_API_DATA_DIR" \
  "$GOODMONEYING_APP_REALTIME_COLLECTION_WORKER_DATA_DIR" \
  "$GOODMONEYING_APP_BACKFILL_COLLECTION_WORKER_DATA_DIR" \
  "$GOODMONEYING_APP_CONFIG_DIR"
run_remote_compose "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_BASE_DIR" "web" "web/compose.yml" "$GOODMONEYING_WEB_COMPOSE" "$GOODMONEYING_WEB_DOCKER_CONFIG" \
  "$GOODMONEYING_WEB_NGINX_CACHE_DIR" \
  "$GOODMONEYING_WEB_CONFIG_DIR"
