# Newton Example Studio on Brev

A browser launcher for running selected [Newton](https://github.com/newton-physics/newton)
examples on a GPU instance and viewing their live output in an embedded Rerun Web Viewer.

## Brev Launchable setup

Use VM Mode, attach this repository as the source, and use this as the setup command:

```bash
bash scripts/setup_brev.sh
```

The setup script:

- builds the `newton-gallery` Docker image (pinned Newton revision, Warp, MuJoCo,
  PyTorch CUDA, Rerun SDK, and the four example asset packs);
- starts the stack with `docker compose up -d`;
- installs and enables a systemd unit (`newton-gallery.service`) so
  `docker compose up -d` runs again after every reboot;
- waits for the launcher health endpoint before returning.

Containers also use `restart: unless-stopped`, so if only the Docker daemon
restarts they come back without waiting for systemd.

Requirements on the instance: Docker Engine, Compose plugin, and NVIDIA
Container Toolkit (`gpus: all`). Drivers stay on the host.

Add this optional Text Launch Parameter:

| Setting | Value |
| --- | --- |
| Name | `NEWTON_COMMIT` |
| Required | No |
| Default | Leave empty |
| Description | Full or abbreviated Newton Git commit to check out |

When omitted, the image build uses the validated commit baked into the
Dockerfile / compose defaults. Brev passes a supplied value through as a
build arg.

Expose this single port in the Launchable:

| Port | Service |
| --- | --- |
| `4173` | Example browser, control API, runtime log, and the reverse-proxied Rerun Web Viewer and recording stream |

The launcher reverse-proxies the Rerun Web Viewer (`/viewer`) and its gRPC
recording stream through port `4173`, so the viewer is served same-origin and no
other ports need to be exposed. Rerun still listens inside the container on
`9090`/`9876`.

Open port `4173` when setup completes.

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
| `NEWTON_LAUNCHER_PORT` | `4173` |
| `PREFETCH_NEWTON_ASSETS` | `1` (container start) |
| `RERUN_SERVER_MEMORY_LIMIT` | `16MiB` |
| `RERUN_WEB_ORIGIN` | empty (same-origin proxy) |
| `RERUN_GRPC_ORIGIN` | empty (same-origin proxy) |

Set `PREFETCH_NEWTON_ASSETS=0` to skip asset downloads on first container start
(examples then fetch packs on first run into the `newton-cache` volume).

`RERUN_SERVER_MEMORY_LIMIT` caps the backlog Rerun's gRPC server replays to a
connecting browser. Rerun defaults to 1 GiB, which a viewer opened minutes into a
run must download before it reaches live frames — tens of seconds of apparent
latency. Geometry is logged as static data and is never dropped, so a small
buffer still yields a complete scene; raise it only if timeline scrubbing through
past frames is needed.

Asset prefetch runs at container start, not during `docker build`. BuildKit has
no GPU driver; importing Newton/Warp in a build step only produces a misleading
“CUDA driver not found” warning.

## Local Docker

```bash
docker compose up -d --build
```

Then open [http://127.0.0.1:4173](http://127.0.0.1:4173).

Useful commands:

```bash
docker compose logs -f
docker compose restart
sudo systemctl status newton-gallery.service   # after setup_brev.sh
```

## Local restart (no Docker)

After dependencies have been installed once on the host:

```bash
NEWTON_ROOT=../newton python3 server.py
```

Then open [http://127.0.0.1:4173](http://127.0.0.1:4173).
