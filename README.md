# Newton Example Studio on Brev

A browser launcher for running selected [Newton](https://github.com/newton-physics/newton)
examples on a GPU instance and viewing their live output in an embedded Rerun Web Viewer.

## Brev Launchable setup

Use VM Mode, attach this repository as the source, and use this as the setup command:

```bash
bash scripts/setup_brev.sh
```

Add this optional Text Launch Parameter:

| Setting | Value |
| --- | --- |
| Name | `NEWTON_COMMIT` |
| Required | No |
| Default | Leave empty |
| Description | Full or abbreviated Newton Git commit to check out |

When omitted, the setup script uses the validated commit built into the
launcher. Brev passes a supplied value to the setup script as an environment
variable.

Expose this single port in the Launchable:

| Port | Service |
| --- | --- |
| `4173` | Example browser, control API, runtime log, and the reverse-proxied Rerun Web Viewer and recording stream |

The launcher reverse-proxies the Rerun Web Viewer (`/viewer`) and its gRPC
recording stream through port `4173`, so the viewer is served same-origin and no
other ports need to be exposed. Rerun still listens locally on `9090`/`9876`.

Open port `4173` when setup completes.

The single setup script:

- installs `uv` if needed;
- clones a pinned Newton revision beside this repository;
- runs Newton's nominal `uv sync --extra examples --extra torch-cu12` to build
  its environment from the maintained lockfile (Warp, MuJoCo-Warp, PyTorch, and
  the viewer stack), substituting a stable MuJoCo release for the expired
  nightly the lockfile pins, and adds `rerun-sdk` for the web viewer;
- reverse-proxies the Rerun Web Viewer and gRPC stream through the launcher port;
- prefetches the four externally downloaded example asset packs;
- starts the launcher on `0.0.0.0:4173`;
- waits for the launcher health endpoint before returning.

It does not install drivers or configure the GPU.

## Public Rerun URLs

By default the browser reaches the Rerun viewer and stream same-origin through
the launcher port, so no extra configuration is required.

For advanced setups that expose the Rerun ports (`9090`/`9876`) directly instead
of using the built-in proxy, override the origins as Launchable environment
variables:

```bash
RERUN_WEB_ORIGIN=https://RERUN-WEB-LINK \
RERUN_GRPC_ORIGIN=https://RERUN-GRPC-LINK \
bash scripts/setup_brev.sh
```

The values are ordinary `http://` or `https://` origins. The frontend adds
Rerun's `rerun+` transport prefix.

## Optional setup variables

| Variable | Default |
| --- | --- |
| `NEWTON_COMMIT` | Validated pinned commit |
| `NEWTON_ROOT` | Sibling `../newton` directory |
| `PYTHON_BIN` | `python3` |
| `NEWTON_LAUNCHER_PORT` | `4173` |
| `RERUN_WEB_PORT` | `9090` |
| `RERUN_GRPC_PORT` | `9876` |
| `PREFETCH_NEWTON_ASSETS` | `1` |

Set `PREFETCH_NEWTON_ASSETS=0` to defer asset downloads until examples launch.

## Local restart

After dependencies have been installed once:

```bash
NEWTON_ROOT=../newton python3 server.py
```

Then open [http://127.0.0.1:4173](http://127.0.0.1:4173).
# newton-gallery
# newton-gallery
