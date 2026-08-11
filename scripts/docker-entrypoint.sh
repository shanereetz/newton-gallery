#!/bin/bash
set -Eeuo pipefail

# Ensure writable cache dirs exist even when volumes overlay the image paths.
mkdir -p "${NEWTON_CACHE_PATH:-/var/cache/newton}" "${HOME}/.cache/warp"

# Optional first-boot asset fetch when the volume is empty (image may already
# contain them; this covers a fresh named volume without a rebuild).
if [[ "${PREFETCH_NEWTON_ASSETS:-0}" == "1" ]]; then
  NEWTON_PYTHON="${NEWTON_ROOT}/.venv/bin/python"
  if [[ -x "${NEWTON_PYTHON}" ]]; then
    "${NEWTON_PYTHON}" - <<'PY' || true
import os
from pathlib import Path
import newton

cache = Path(os.environ.get("NEWTON_CACHE_PATH", "/var/cache/newton"))
# Presence of any asset pack means a previous prefetch already ran.
if not any(cache.glob("*")):
    for name in ("unitree_g1", "anybotics_anymal_c", "franka_emika_panda", "style3d"):
        newton.utils.download_asset(name)
PY
  fi
fi

exec "$@"
