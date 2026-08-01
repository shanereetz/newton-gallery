#!/bin/bash
set -Eeuo pipefail

LAUNCHER_DIR="${LAUNCHER_DIR:-${HOME}/newton-brev-launcher}"
WORKSPACE_DIR="${WORKSPACE_DIR:-${HOME}}"

if [[ ! -f "${LAUNCHER_DIR}/server.py" ]]; then
  echo "Launcher repository not found at ${LAUNCHER_DIR}" >&2
  echo "Attach the newton-brev-launcher repository as the Launchable source." >&2
  exit 1
fi

NEWTON_REPOSITORY="${NEWTON_REPOSITORY:-https://github.com/newton-physics/newton.git}"
DEFAULT_NEWTON_COMMIT="045db424a2638eb8e3abd42795584e4eaf96dcfc"
NEWTON_COMMIT="${NEWTON_COMMIT:-${DEFAULT_NEWTON_COMMIT}}"
NEWTON_ROOT="${NEWTON_ROOT:-${WORKSPACE_DIR}/newton}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LAUNCHER_PORT="${NEWTON_LAUNCHER_PORT:-4173}"
WEB_PORT="${RERUN_WEB_PORT:-9090}"
GRPC_PORT="${RERUN_GRPC_PORT:-9876}"

if [[ ! "${NEWTON_COMMIT}" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
  echo "NEWTON_COMMIT must be a 7-40 character hexadecimal Git commit." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  UV_INSTALLER="$(mktemp)"
  curl -LsSf https://astral.sh/uv/install.sh -o "${UV_INSTALLER}"
  sh "${UV_INSTALLER}"
  rm -f "${UV_INSTALLER}"
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
fi

UV_BIN="$(command -v uv)"

if [[ ! -d "${NEWTON_ROOT}/.git" ]]; then
  git clone --filter=blob:none "${NEWTON_REPOSITORY}" "${NEWTON_ROOT}"
fi

if [[ -n "$(git -C "${NEWTON_ROOT}" status --porcelain)" ]]; then
  echo "Refusing to change a dirty Newton checkout at ${NEWTON_ROOT}" >&2
  exit 1
fi

git -C "${NEWTON_ROOT}" fetch --prune origin
if ! RESOLVED_NEWTON_COMMIT="$(git -C "${NEWTON_ROOT}" rev-parse --verify "${NEWTON_COMMIT}^{commit}")"; then
  echo "NEWTON_COMMIT does not resolve to a fetched Newton commit: ${NEWTON_COMMIT}" >&2
  exit 1
fi
git -C "${NEWTON_ROOT}" checkout --detach "${RESOLVED_NEWTON_COMMIT}"

NEWTON_PYTHON="${NEWTON_ROOT}/.venv/bin/python"

# Install Newton and its dependencies straight from the maintained uv.lock,
# following Newton's nominal `uv sync --extra examples --extra torch-cu12`
# workflow. This pulls the exact versions Newton tests (Warp, PyTorch,
# MuJoCo-Warp, the viewer stack) and installs Newton itself editable.
#
# One deviation is required: this commit's lockfile pins a MuJoCo nightly that
# the upstream index only retains for a few weeks, so `uv sync` cannot fetch it.
# Skip that single package during sync and install the compatible stable
# release afterwards. MuJoCo-Warp requires mujoco>=3.3.7, and 3.3.7 is the
# latest stable release in the pinned 3.3 series.
(cd "${NEWTON_ROOT}" && "${UV_BIN}" sync --python "${PYTHON_BIN}" \
  --extra examples --extra torch-cu12 --no-install-package mujoco)
"${UV_BIN}" pip install --python "${NEWTON_PYTHON}" "mujoco==3.3.7"

# rerun-sdk powers the embedded web viewer and is not part of Newton's
# examples dependency set.
"${UV_BIN}" pip install --python "${NEWTON_PYTHON}" "rerun-sdk>=0.35.0"

if [[ "${PREFETCH_NEWTON_ASSETS:-1}" == "1" ]]; then
  "${NEWTON_PYTHON}" -c 'import newton; [newton.utils.download_asset(name) for name in ("unitree_g1", "anybotics_anymal_c", "franka_emika_panda", "style3d")]'
fi

PID_FILE="${LAUNCHER_DIR}/.launcher.pid"
LOG_FILE="${LAUNCHER_DIR}/.launcher.log"

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(<"${PID_FILE}")"
  if [[ "${OLD_PID}" =~ ^[0-9]+$ ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    kill "${OLD_PID}"
  fi
fi

cd "${LAUNCHER_DIR}"
nohup env NEWTON_ROOT="${NEWTON_ROOT}" NEWTON_LAUNCHER_HOST="0.0.0.0" NEWTON_LAUNCHER_PORT="${LAUNCHER_PORT}" RERUN_WEB_PORT="${WEB_PORT}" RERUN_GRPC_PORT="${GRPC_PORT}" RERUN_WEB_ORIGIN="${RERUN_WEB_ORIGIN:-}" RERUN_GRPC_ORIGIN="${RERUN_GRPC_ORIGIN:-}" python3 server.py >"${LOG_FILE}" 2>&1 &
echo "$!" >"${PID_FILE}"

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${LAUNCHER_PORT}/api/status" >/dev/null; then
    echo "Newton Example Studio is ready on port ${LAUNCHER_PORT}."
    echo "Expose port ${LAUNCHER_PORT} in the Brev Launchable; the embedded Rerun"
    echo "viewer and gRPC stream are proxied through it."
    exit 0
  fi
  sleep 1
done

echo "Launcher did not become ready. See ${LOG_FILE}." >&2
exit 1
