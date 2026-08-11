#!/bin/bash
set -Eeuo pipefail

# Brev Launchable setup: build the Newton Example Studio image, start it with
# Docker Compose, and install a systemd unit so the stack returns on reboot.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
UNIT_NAME="newton-gallery.service"
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"

if [[ ! -f "${LAUNCHER_DIR}/server.py" || ! -f "${LAUNCHER_DIR}/docker-compose.yml" ]]; then
  echo "Launcher repository incomplete at ${LAUNCHER_DIR}" >&2
  echo "Attach this repository as the Launchable source." >&2
  exit 1
fi

DEFAULT_NEWTON_COMMIT="045db424a2638eb8e3abd42795584e4eaf96dcfc"
export NEWTON_COMMIT="${NEWTON_COMMIT:-${DEFAULT_NEWTON_COMMIT}}"
export NEWTON_LAUNCHER_PORT="${NEWTON_LAUNCHER_PORT:-4173}"
export PREFETCH_NEWTON_ASSETS="${PREFETCH_NEWTON_ASSETS:-1}"
export RERUN_WEB_ORIGIN="${RERUN_WEB_ORIGIN:-}"
export RERUN_GRPC_ORIGIN="${RERUN_GRPC_ORIGIN:-}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-newton-gallery}"

if [[ ! "${NEWTON_COMMIT}" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
  echo "NEWTON_COMMIT must be a 7-40 character hexadecimal Git commit." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Engine before running this setup." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required (docker compose)." >&2
  exit 1
fi

# NVIDIA Container Toolkit must be present for gpus: all. Brev GPU images
# normally ship this; fail loudly if the CDI/runtime is missing.
if ! docker info 2>/dev/null | grep -qiE 'Runtimes:.*nvidia|nvidia.com/gpu'; then
  echo "NVIDIA Container Toolkit runtime not detected." >&2
  echo "Install nvidia-container-toolkit and restart Docker, then re-run setup." >&2
  exit 1
fi

# Stop any leftover bare-metal launcher from older setup scripts.
if [[ -f "${LAUNCHER_DIR}/.launcher.pid" ]]; then
  OLD_PID="$(<"${LAUNCHER_DIR}/.launcher.pid")"
  if [[ "${OLD_PID}" =~ ^[0-9]+$ ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    kill "${OLD_PID}" || true
  fi
  rm -f "${LAUNCHER_DIR}/.launcher.pid"
fi

cd "${LAUNCHER_DIR}"

echo "Building Newton Example Studio image (Newton ${NEWTON_COMMIT})…"
docker compose build

echo "Starting compose stack…"
docker compose up -d --remove-orphans

# Install / refresh the systemd unit so reboot brings the stack back.
if command -v systemctl >/dev/null 2>&1 && [[ -d /etc/systemd/system ]]; then
  TMP_UNIT="$(mktemp)"
  sed "s|__LAUNCHER_DIR__|${LAUNCHER_DIR}|g" \
    "${SCRIPT_DIR}/newton-gallery.service.in" >"${TMP_UNIT}"
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo install -m 644 "${TMP_UNIT}" "${UNIT_PATH}"
    sudo systemctl daemon-reload
    sudo systemctl enable "${UNIT_NAME}"
  elif [[ "$(id -u)" -eq 0 ]]; then
    install -m 644 "${TMP_UNIT}" "${UNIT_PATH}"
    systemctl daemon-reload
    systemctl enable "${UNIT_NAME}"
  else
    echo "Warning: could not install ${UNIT_NAME} (no root/sudo)." >&2
    echo "Containers still use restart: unless-stopped via the Docker daemon." >&2
  fi
  rm -f "${TMP_UNIT}"
else
  echo "Warning: systemd unavailable; relying on Docker restart policies only." >&2
fi

echo "Waiting for launcher health on port ${NEWTON_LAUNCHER_PORT}…"
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${NEWTON_LAUNCHER_PORT}/api/status" >/dev/null 2>&1; then
    echo "Newton Example Studio is ready on port ${NEWTON_LAUNCHER_PORT}."
    echo "Expose port ${NEWTON_LAUNCHER_PORT} in the Brev Launchable; the embedded"
    echo "Rerun viewer and gRPC stream are proxied through it."
    echo "On reboot: systemd unit ${UNIT_NAME} runs docker compose up -d, and"
    echo "the container restart policy is unless-stopped."
    exit 0
  fi
  sleep 2
done

echo "Launcher did not become ready. Recent compose logs:" >&2
docker compose logs --tail=80 >&2 || true
exit 1
