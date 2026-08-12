#!/bin/bash
set -Eeuo pipefail

# Ensure writable cache dirs exist even when volumes overlay the image paths.
mkdir -p "${NEWTON_CACHE_PATH:-/var/cache/newton}" "${HOME}/.cache/warp"

NEWTON_PYTHON="${NEWTON_ROOT}/.venv/bin/python"

# Fail fast if the container was started without GPU passthrough. Warp will
# otherwise quietly fall back to CPU and every example will look "broken."
if [[ -x "${NEWTON_PYTHON}" ]]; then
  "${NEWTON_PYTHON}" - <<'PY'
import sys
import warp as wp

wp.init()
devices = [d for d in wp.get_devices() if d.is_cuda]
if not devices:
    print(
        "ERROR: No CUDA device visible to Warp. "
        "The container needs GPU access (compose `gpus: all` / NVIDIA Container Toolkit).",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"Warp CUDA ok: {[str(d) for d in devices]}", flush=True)
PY
fi

# First-boot asset fetch into the named volume (skipped when cache already warm).
if [[ "${PREFETCH_NEWTON_ASSETS:-1}" == "1" && -x "${NEWTON_PYTHON}" ]]; then
  "${NEWTON_PYTHON}" - <<'PY'
import os
from pathlib import Path
import newton

cache = Path(os.environ.get("NEWTON_CACHE_PATH", "/var/cache/newton"))
if not any(cache.glob("*")):
    print("Prefetching Newton example assets…", flush=True)
    for name in ("unitree_g1", "anybotics_anymal_c", "franka_emika_panda", "style3d"):
        newton.utils.download_asset(name)
    print("Asset prefetch complete.", flush=True)
PY
fi

exec "$@"
