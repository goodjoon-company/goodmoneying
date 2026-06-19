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

PROFILE_DIR="$ROOT_DIR/deploy/profiles/$PROFILE"
source "$PROFILE_DIR/profile.env"
source "$PROFILE_DIR/hosts.env"

if [[ "$DRY_RUN" == "1" ]]; then
  printf 'profile=%s\n' "$GOODMONEYING_DEPLOY_PROFILE"
  printf 'tag=%s\n' "$IMAGE_TAG"
  printf 'infra host=%s compose=%s\n' "$GOODMONEYING_INFRA_HOST" "$GOODMONEYING_INFRA_COMPOSE"
  printf 'app host=%s compose=%s\n' "$GOODMONEYING_APP_HOST" "$GOODMONEYING_APP_COMPOSE"
  printf 'web host=%s compose=%s\n' "$GOODMONEYING_WEB_HOST" "$GOODMONEYING_WEB_COMPOSE"
  exit 0
fi

printf 'prod-home 배포를 시작합니다. tag=%s\n' "$IMAGE_TAG"
