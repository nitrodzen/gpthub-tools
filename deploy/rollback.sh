#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/gpthub-tools}"
TARGET="${1:?Usage: rollback.sh RELEASE_ID}"

if [[ ! "$TARGET" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Invalid release ID: $TARGET" >&2
  exit 2
fi

RELEASE_DIR="$APP_ROOT/releases/$TARGET"
if [[ ! -d "$RELEASE_DIR" ]]; then
  echo "Unknown release: $TARGET" >&2
  exit 2
fi
RELEASE_DIR="$(cd "$RELEASE_DIR" && pwd -P)"
case "$RELEASE_DIR" in
  "$APP_ROOT"/releases/*) ;;
  *) echo "Release directory must be inside $APP_ROOT/releases" >&2; exit 2 ;;
esac

atomic_symlink() {
  local target="$1"
  local link="$2"
  local temporary="${link}.tmp.$$"

  ln -s "$target" "$temporary"
  if ! mv -Tf "$temporary" "$link"; then
    rm -f "$temporary"
    return 1
  fi
}

release_target() {
  local link="$1"
  local target

  [[ -L "$link" ]] || return 1
  target="$(readlink -f "$link")" || return 1
  case "$target" in
    "$APP_ROOT"/releases/*) [[ -d "$target" ]] || return 1 ;;
    *) return 1 ;;
  esac
  printf '%s\n' "$target"
}

wait_for_release() {
  local release_id="$1"
  local health_payload

  for _attempt in $(seq 1 60); do
    health_payload="$(curl -fsS http://127.0.0.1:9080/api/health 2>/dev/null || true)"
    if grep -Fq '"status":"ok"' <<< "$health_payload" &&
       grep -Fq "\"version\":\"$release_id\"" <<< "$health_payload"; then
      return 0
    fi
    sleep 5
  done
  return 1
}

restore_old_stack() {
  local old_release="$1"
  local old_release_id

  if [[ -z "$old_release" ]]; then
    (cd "$RELEASE_DIR" && docker compose down --remove-orphans)
    return
  fi
  old_release_id="$(basename "$old_release")"
  (
    cd "$old_release"
    export APP_VERSION="$old_release_id"
    docker compose up -d --remove-orphans
  )
  wait_for_release "$old_release_id"
}

old_current="$(release_target "$APP_ROOT/current" || true)"

cd "$RELEASE_DIR"
ln -sfn "$APP_ROOT/shared/data" data
ln -sfn "$APP_ROOT/shared/.env" .env
export APP_VERSION="$TARGET"
docker compose config --quiet
if ! docker compose up -d --remove-orphans || ! wait_for_release "$TARGET"; then
  echo "Rollback target failed its exact-version health check; restoring the live release" >&2
  restore_old_stack "$old_current"
  exit 4
fi

if [[ -n "$old_current" && "$old_current" != "$RELEASE_DIR" ]] &&
   ! atomic_symlink "$old_current" "$APP_ROOT/previous"; then
  echo "Could not update the previous release link; restoring the live release" >&2
  restore_old_stack "$old_current"
  exit 4
fi
if ! atomic_symlink "$RELEASE_DIR" "$APP_ROOT/current"; then
  echo "Could not update the current release link; restoring the live release" >&2
  restore_old_stack "$old_current"
  exit 4
fi

printf 'Rolled back to %s\n' "$TARGET"
