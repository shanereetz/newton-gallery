# Newton Example Studio — GPU launcher with embedded Rerun viewer.
# Drivers stay on the host; this image only needs the CUDA runtime libs.
FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

ARG NEWTON_COMMIT=045db424a2638eb8e3abd42795584e4eaf96dcfc
ARG NEWTON_REPOSITORY=https://github.com/newton-physics/newton.git
ARG PREFETCH_NEWTON_ASSETS=1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEWTON_ROOT=/opt/newton \
    NEWTON_CACHE_PATH=/var/cache/newton \
    NEWTON_LAUNCHER_HOST=0.0.0.0 \
    NEWTON_LAUNCHER_PORT=4173 \
    RERUN_WEB_PORT=9090 \
    RERUN_GRPC_PORT=9876 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics \
    PATH=/root/.local/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      python3 \
      python3-venv \
      python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /opt

# Pin Newton to the validated commit and install from its lockfile.
# One deviation from nominal sync: this commit pins an expired MuJoCo nightly,
# so skip that package and install the compatible stable 3.3.7 release after.
RUN git clone --filter=blob:none "${NEWTON_REPOSITORY}" "${NEWTON_ROOT}" \
 && git -C "${NEWTON_ROOT}" fetch --prune origin \
 && git -C "${NEWTON_ROOT}" checkout --detach "${NEWTON_COMMIT}" \
 && cd "${NEWTON_ROOT}" \
 && uv sync --python python3 --extra examples --extra torch-cu12 --no-install-package mujoco \
 && uv pip install --python "${NEWTON_ROOT}/.venv/bin/python" "mujoco==3.3.7" \
 && uv pip install --python "${NEWTON_ROOT}/.venv/bin/python" "rerun-sdk>=0.35.0"

WORKDIR /app
COPY index.html styles.css app.js server.py run_example_realtime.py ./
COPY public ./public
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
 && mkdir -p "${NEWTON_CACHE_PATH}"

# Prefetch the four allowlisted example asset packs into the image so first
# launch on a fresh Brev instance does not block on git clones. Cache mounts
# in compose can still overlay this path at runtime.
RUN if [ "${PREFETCH_NEWTON_ASSETS}" = "1" ]; then \
      "${NEWTON_ROOT}/.venv/bin/python" -c \
        'import newton; [newton.utils.download_asset(n) for n in ("unitree_g1","anybotics_anymal_c","franka_emika_panda","style3d")]'; \
    fi

EXPOSE 4173

HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=20 \
  CMD curl -fsS "http://127.0.0.1:${NEWTON_LAUNCHER_PORT}/api/status" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python3", "server.py"]
