#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/gpthub-tools}"
TARGET="${1:?Usage: rollback.sh RELEASE_ID}"
RELEASE_DIR="$APP_ROOT/releases/$TARGET"

if [[ ! -d "$RELEASE_DIR" ]]; then
  echo "Unknown release: $TARGET" >&2
  exit 2
fi

cd "$RELEASE_DIR"
ln -sfn "$APP_ROOT/shared/data" data
ln -sfn "$APP_ROOT/shared/.env" .env
export APP_VERSION="$TARGET"
docker compose up -d --remove-orphans
curl -fsS http://127.0.0.1:9080/api/health
ln -sfn "$RELEASE_DIR" "$APP_ROOT/current"
printf 'Rolled back to %s\n' "$TARGET"
