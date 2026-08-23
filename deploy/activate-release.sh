#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/gpthub-tools}"
RELEASE_DIR="$(pwd -P)"
RELEASE_ID="${1:-$(basename "$RELEASE_DIR")}"
DEPLOY_MIN_FREE_MIB="${DEPLOY_MIN_FREE_MIB:-5120}"
DEPLOY_BACKEND_IMAGE_BYTES="${DEPLOY_BACKEND_IMAGE_BYTES:-}"

if [[ ! "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Invalid release ID: $RELEASE_ID" >&2
  exit 2
fi
case "$RELEASE_DIR" in
  "$APP_ROOT"/releases/*) ;;
  *) echo "Release directory must be inside $APP_ROOT/releases" >&2; exit 2 ;;
esac

if [[ ! "$DEPLOY_MIN_FREE_MIB" =~ ^[0-9]+$ ]]; then
  echo "DEPLOY_MIN_FREE_MIB must be a non-negative integer" >&2
  exit 2
fi
if [[ ! "$DEPLOY_BACKEND_IMAGE_BYTES" =~ ^[1-9][0-9]*$ ]]; then
  echo "DEPLOY_BACKEND_IMAGE_BYTES must be the measured positive byte size of the locally verified backend image" >&2
  exit 2
fi

image_formula_mib="$(((2 * DEPLOY_BACKEND_IMAGE_BYTES + 1073741824 + 1048575) / 1048576))"
required_free_mib=5120
if (( image_formula_mib > required_free_mib )); then
  required_free_mib="$image_formula_mib"
fi
if (( DEPLOY_MIN_FREE_MIB > required_free_mib )); then
  required_free_mib="$DEPLOY_MIN_FREE_MIB"
fi

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

cleanup_old_project_images() {
  local previous_release_id="$1"
  local image_ref
  local used_by
  local refs

  if ! refs="$(docker image ls --format '{{.Repository}}:{{.Tag}}')"; then
    echo "Warning: could not list Docker images; skipping project image cleanup" >&2
    return 0
  fi

  while IFS= read -r image_ref; do
    case "$image_ref" in
      gpthub-tools-backend:*|gpthub-tools-gateway:*) ;;
      *) continue ;;
    esac

    if [[ "$image_ref" == "gpthub-tools-backend:$RELEASE_ID" ||
          "$image_ref" == "gpthub-tools-gateway:$RELEASE_ID" ||
          ( -n "$previous_release_id" && "$image_ref" == "gpthub-tools-backend:$previous_release_id" ) ||
          ( -n "$previous_release_id" && "$image_ref" == "gpthub-tools-gateway:$previous_release_id" ) ]]; then
      continue
    fi

    used_by="$(docker ps -aq --filter "ancestor=$image_ref" 2>/dev/null || true)"
    if [[ -n "$used_by" ]]; then
      printf 'Keeping project image still used by a container: %s\n' "$image_ref"
      continue
    fi

    if docker image rm "$image_ref" >/dev/null; then
      printf 'Removed unused old project image: %s\n' "$image_ref"
    else
      printf 'Warning: could not remove old project image: %s\n' "$image_ref" >&2
    fi
  done <<< "$refs"
}

restore_old_stack() {
  local old_release="$1"
  local old_release_id
  local old_health

  if [[ -z "$old_release" ]]; then
    echo "No previous live release exists; stopping the failed new stack" >&2
    (
      cd "$RELEASE_DIR"
      docker compose down --remove-orphans
    )
    return
  fi
  old_release_id="$(basename "$old_release")"
  printf 'Restoring previous live stack from %s\n' "$old_release_id" >&2
  (
    cd "$old_release"
    export APP_VERSION="$old_release_id"
    docker compose up -d --remove-orphans
  )
  for _attempt in $(seq 1 60); do
    old_health="$(curl -fsS http://127.0.0.1:9080/api/health 2>/dev/null || true)"
    if grep -Fq '"status":"ok"' <<< "$old_health" &&
       grep -Fq "\"version\":\"$old_release_id\"" <<< "$old_health"; then
      printf 'Restored live stack %s\n' "$old_release_id" >&2
      return 0
    fi
    sleep 5
  done
  echo "Previous live stack did not recover" >&2
  return 1
}

old_current="$(release_target "$APP_ROOT/current" || true)"

available_kib="$(df -Pk "$APP_ROOT" | awk 'NR == 2 {print $4}')"
required_kib="$((required_free_mib * 1024))"
if [[ ! "$available_kib" =~ ^[0-9]+$ ]]; then
  echo "Could not determine free disk space for $APP_ROOT" >&2
  exit 5
fi
if (( available_kib < required_kib )); then
  printf 'Deployment requires at least %s MiB free under %s; only %s MiB is available\n' \
    "$required_free_mib" "$APP_ROOT" "$((available_kib / 1024))" >&2
  exit 5
fi
printf 'Disk preflight passed: %s MiB free (minimum %s MiB)\n' \
  "$((available_kib / 1024))" "$required_free_mib"
printf 'Measured backend image: %s bytes; required free-space formula: max(5120 MiB, %s MiB)\n' \
  "$DEPLOY_BACKEND_IMAGE_BYTES" "$image_formula_mib"

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
if ! docker compose up -d --remove-orphans; then
  echo "Starting the new stack failed; current symlink was not changed" >&2
  restore_old_stack "$old_current"
  exit 4
fi

for attempt in $(seq 1 60); do
  health_payload="$(curl -fsS http://127.0.0.1:9080/api/health 2>/dev/null || true)"
  if grep -Fq '"status":"ok"' <<< "$health_payload" &&
     grep -Fq "\"version\":\"$RELEASE_ID\"" <<< "$health_payload"; then
    if [[ -n "$old_current" && "$old_current" != "$RELEASE_DIR" ]] &&
       ! atomic_symlink "$old_current" "$APP_ROOT/previous"; then
      echo "Could not update the previous release link; restoring the old stack" >&2
      restore_old_stack "$old_current"
      exit 4
    fi
    if ! atomic_symlink "$RELEASE_DIR" "$APP_ROOT/current"; then
      echo "Could not update the current release link; restoring the old stack" >&2
      restore_old_stack "$old_current"
      exit 4
    fi
    previous_release="$(release_target "$APP_ROOT/previous" || true)"
    previous_release_id="${previous_release:+$(basename "$previous_release")}"
    cleanup_old_project_images "$previous_release_id"
    printf 'Activated %s\n' "$RELEASE_ID"
    exit 0
  fi
  sleep 5
done

echo "Health check failed; current symlink was not changed" >&2
docker compose ps || true
restore_old_stack "$old_current"
exit 4
