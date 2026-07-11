#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/gpthub-tools}"
RELEASE_DIR="$(pwd -P)"
RELEASE_ID="${1:-$(basename "$RELEASE_DIR")}"

case "$RELEASE_DIR" in
  "$APP_ROOT"/releases/*) ;;
  *) echo "Release directory must be inside $APP_ROOT/releases" >&2; exit 2 ;;
esac

mkdir -p "$APP_ROOT/shared/data/jobs" "$APP_ROOT/shared/data/metrics"
chown -R 10001:10001 "$APP_ROOT/shared/data"
chmod 700 "$APP_ROOT/shared/data/jobs"
chmod 700 "$APP_ROOT/shared/data/metrics"

if [[ ! -f "$APP_ROOT/shared/.env" ]]; then
  echo "Missing $APP_ROOT/shared/.env" >&2
  exit 3
fi

ln -sfn "$APP_ROOT/shared/data" "$RELEASE_DIR/data"
ln -sfn "$APP_ROOT/shared/.env" "$RELEASE_DIR/.env"

export APP_VERSION="$RELEASE_ID"
docker compose config --quiet
docker compose build --pull gateway api
docker compose up -d --remove-orphans

for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:9080/api/health | grep -q '"status":"ok"'; then
    ln -sfn "$RELEASE_DIR" "$APP_ROOT/current"
    printf 'Activated %s\n' "$RELEASE_ID"
    exit 0
  fi
  sleep 5
done

echo "Health check failed; current symlink was not changed" >&2
docker compose ps
exit 4
