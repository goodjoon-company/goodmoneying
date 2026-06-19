#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ENV="$SCRIPT_DIR/deploy.compose.env"
COMPOSE_FILE="$SCRIPT_DIR/compose.app.yml"

set -a
source "$COMPOSE_ENV"
set +a
export DOCKER_CONFIG="${GOODMONEYING_DOCKER_CONFIG:-$HOME/.docker}"

docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" stop api "$@"
