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

# Warp compiles an example's kernels on its first run and caches them under
# ~/.cache/warp, which is a named volume. Uncached, the heavy scenes take two to
# four minutes to reach a first frame; cached, all of them take under ten
# seconds. Compile them once in the background so the launcher is reachable
# immediately and only the launches during this window are slow.
WARM_MARKER="${HOME}/.cache/warp/.gallery-warmed"
if [[ "${WARM_KERNEL_CACHE:-1}" == "1" && -x "${NEWTON_PYTHON}" && ! -f "${WARM_MARKER}" ]]; then
  echo "Warming the Warp kernel cache in the background (one time, ~15 min)…" >&2
  (
    for name in basic_shapes robot_g1 robot_anymal_c_walk cloth_franka \
                mpm_twoway_coupling ik_franka diffsim_drone cloth_style3d; do
      nice -n 10 "${NEWTON_PYTHON}" -m newton.examples "${name}" \
        --viewer null --num-frames 2 --device cuda:0 >/dev/null 2>&1 || \
        echo "Kernel warm-up failed for ${name}; it will compile on first launch." >&2
    done
    touch "${WARM_MARKER}"
    echo "Warp kernel cache warm; every example now starts in seconds." >&2
  ) &
fi

exec "$@"
