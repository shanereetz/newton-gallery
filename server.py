#!/usr/bin/env python3
"""Local control plane for the Newton-on-Brev browser viewer prototype."""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
NEWTON_ROOT = Path(os.environ.get("NEWTON_ROOT", ROOT.parent / "newton")).resolve()
NEWTON_PYTHON = NEWTON_ROOT / ".venv" / "bin" / "python"
HOST = os.environ.get("NEWTON_LAUNCHER_HOST", "0.0.0.0")
PORT = int(os.environ.get("NEWTON_LAUNCHER_PORT", "4173"))
VIEWER_PORT = int(os.environ.get("RERUN_WEB_PORT", "9090"))
GRPC_PORT = int(os.environ.get("RERUN_GRPC_PORT", "9876"))
WEB_UPSTREAM = ("127.0.0.1", VIEWER_PORT)
GRPC_UPSTREAM = ("127.0.0.1", GRPC_PORT)
VIEWER_ORIGIN = os.environ.get("RERUN_WEB_ORIGIN", "").rstrip("/") or None
GRPC_ORIGIN = os.environ.get("RERUN_GRPC_ORIGIN", "").rstrip("/") or None
LOG_PATH = ROOT / ".newton-viewer.log"
DEBUG_LOG_PATH = Path("/home/sreetz/Desktop/newton-gallery/.cursor/debug-72d3bf.log")
DEBUG_RUN_ID = f"server-{os.getpid()}-{int(time.time())}"

STAGE_MARKER = "NEWTON_LAUNCHER_STAGE:"
PORT_RELEASE_TIMEOUT = 15.0

# Floor and ceiling percentages per phase. Newton reports no load percentage,
# so the bar is driven by observed milestones: the floor is what has provably
# happened, and the frontend only creeps toward the ceiling of the current
# phase so the bar keeps moving without claiming unearned progress.
PHASE_BOUNDS = {
    "starting": (3, 10),
    "importing": (10, 22),
    "assets": (22, 45),
    "building": (30, 60),
    "connecting": (62, 78),
    "warming": (80, 92),
    "streaming": (94, 99),
    "offline": (0, 0),
    "failed": (0, 0),
}

# The Rerun viewer bundle is a ~40 MB uncompressed wasm file and its upstream
# server sends neither compression nor cache validators. Over a remote tunnel
# that is a multi-minute download repeated on every reload, so the launcher
# fetches these assets once, keeps a gzipped copy, and serves them with an ETag.
VIEWER_ASSET_SUFFIXES = (".wasm", ".js", ".css", ".json", ".svg", ".png", ".ico", ".woff2")
COMPRESSIBLE_SUFFIXES = (".wasm", ".js", ".css", ".json", ".svg")
VIEWER_ASSET_MAX_BYTES = 128 * 1024 * 1024
VIEWER_ASSET_MAX_AGE = 86400

ALLOWED_EXAMPLES = {
    "basic_shapes",
    "robot_g1",
    "robot_anymal_c_walk",
    "cloth_franka",
    "mpm_twoway_coupling",
    "ik_franka",
    "diffsim_drone",
    "cloth_style3d",
}


def debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": "72d3bf",
        "runId": DEBUG_RUN_ID,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass


def port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.15):
            return True
    except OSError:
        return False


def stage_markers(log_text: str) -> set[str]:
    return {
        line[len(STAGE_MARKER) :].strip() for line in log_text.splitlines() if line.startswith(STAGE_MARKER)
    }


def viewer_ports_busy() -> bool:
    return port_is_open(VIEWER_PORT) or port_is_open(GRPC_PORT)


def kill_stray_runners(keep_pid: int | None = None) -> None:
    """Kill example processes this launcher does not own.

    A runner orphaned by a previous launcher (or a crashed container) keeps
    listening on the Rerun ports, so the next launch either fails to bind or the
    browser attaches to the old stream and the scene appears not to restart.
    """
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid() or pid == keep_pid:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"run_example_realtime.py" not in cmdline:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


class NewtonRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._example: str | None = None
        self._started_at: float | None = None
        self._log_handle = None
        self._last_error: str | None = None
        self._run_id = 0

    def start(self, example: str) -> dict:
        if example not in ALLOWED_EXAMPLES:
            raise ValueError(f"Unsupported example: {example}")
        if not NEWTON_PYTHON.is_file():
            raise RuntimeError(f"Newton Python not found: {NEWTON_PYTHON}")

        with self._lock:
            self._stop_locked()
            LOG_PATH.write_text("", encoding="utf-8")
            self._log_handle = LOG_PATH.open("ab", buffering=0)
            command = [
                str(NEWTON_PYTHON),
                str(ROOT / "run_example_realtime.py"),
                example,
                "--viewer",
                "rerun",
                "--device",
                "cuda:0",
            ]
            self._process = subprocess.Popen(
                command,
                cwd=NEWTON_ROOT,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            self._example = example
            self._started_at = time.time()
            self._last_error = None
            self._run_id += 1
            return self.status()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self._process = None
        self._started_at = None
        kill_stray_runners()
        # Rerun's ports must be free before the next launch: binding fails if
        # they are held, and a browser that connects while the dying stream is
        # still listening sees the previous scene.
        deadline = time.monotonic() + PORT_RELEASE_TIMEOUT
        while viewer_ports_busy() and time.monotonic() < deadline:
            time.sleep(0.2)

    def _read_log(self) -> str:
        try:
            return LOG_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def progress(self, running: bool, viewer_ready: bool, log_text: str) -> dict:
        elapsed = time.time() - self._started_at if self._started_at else 0.0
        if not running:
            phase = "failed" if self._last_error else "offline"
            floor, ceiling = PHASE_BOUNDS[phase]
            return {
                "phase": phase,
                "percent": floor,
                "ceiling": ceiling,
                "detail": self._last_error or "Newton is not running.",
                "elapsed": elapsed,
                "has_markers": False,
            }

        stages = stage_markers(log_text)
        module_loads = log_text.count(" load on device ")
        compiled = module_loads - log_text.count("(cached)")
        downloading = log_text.rfind("Cloning ") > log_text.rfind("Successfully downloaded")

        if "streaming" in stages:
            phase, detail = "streaming", "Streaming live frames to the viewer."
        elif "scene" in stages:
            if viewer_ready:
                phase, detail = "warming", "Scene built. Waiting for the first simulated frame."
            else:
                phase, detail = "connecting", "Scene built. Opening the Rerun recording stream."
        elif downloading:
            phase, detail = "assets", "Downloading example assets from the Newton asset repository."
        elif module_loads:
            phase = "building"
            detail = f"Building the scene · {module_loads} Warp modules loaded"
            if compiled > 0:
                detail += f" · {compiled} compiled from source"
        elif "Warp" in log_text and "initialized" in log_text:
            phase, detail = "importing", "Warp initialized. Importing the example and preparing the model."
        else:
            phase, detail = "starting", "Starting the Newton process on CUDA."

        floor, ceiling = PHASE_BOUNDS[phase]
        if phase == "building":
            # Each loaded Warp module is real progress, but the total is unknown,
            # so the floor approaches the phase ceiling without reaching it.
            floor = min(ceiling - 4, floor + module_loads // 3)
        return {
            "phase": phase,
            "percent": floor,
            "ceiling": ceiling,
            "detail": detail,
            "elapsed": elapsed,
            "has_markers": bool(stages),
        }

    def status(self) -> dict:
        process = self._process
        return_code = process.poll() if process is not None else None
        running = process is not None and return_code is None
        if process is not None and return_code is not None and self._last_error is None:
            self._last_error = f"Newton exited with code {return_code}"
        log_text = self._read_log() if running else ""
        # Open ports alone are not proof of readiness: a stopping run can still be
        # listening. The scene marker is written by this run only, so it ties the
        # Rerun server to the process the browser is about to attach to.
        own_stream = bool(stage_markers(log_text) & {"scene", "streaming"})
        viewer_ready = running and own_stream and port_is_open(VIEWER_PORT) and port_is_open(GRPC_PORT)
        return {
            "example": self._example,
            "running": running,
            "viewer_ready": viewer_ready,
            "run_id": self._run_id,
            "progress": self.progress(running, viewer_ready, log_text),
            "pid": process.pid if running else None,
            "started_at": self._started_at,
            "viewer_port": VIEWER_PORT,
            "grpc_port": GRPC_PORT,
            "viewer_origin": VIEWER_ORIGIN,
            "grpc_origin": GRPC_ORIGIN,
            "error": self._last_error,
        }

    def tail_log(self, limit: int = 80) -> list[str]:
        if not LOG_PATH.exists():
            return []
        try:
            lines = [
                line
                for line in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
                if not line.startswith(STAGE_MARKER)
            ]
            return lines[-max(1, min(limit, 200)) :]
        except OSError:
            return []


class ViewerAssetCache:
    """Fetch-once, gzip-once store for the Rerun viewer's static assets."""

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, upstream_path: str) -> dict | None:
        key = urlparse(upstream_path).path
        with self._lock:
            entry = self._entries.get(key)
        if entry is not None:
            return entry
        entry = self._fetch(upstream_path)
        if entry is None:
            return None
        with self._lock:
            return self._entries.setdefault(key, entry)

    def _fetch(self, upstream_path: str) -> dict | None:
        host, port = WEB_UPSTREAM
        connection = http.client.HTTPConnection(host, port, timeout=60)
        try:
            connection.request("GET", upstream_path, headers={"Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status != HTTPStatus.OK or response.getheader("Content-Encoding"):
                return None
            body = response.read()
            content_type = response.getheader("Content-Type", "application/octet-stream")
        except (OSError, http.client.HTTPException):
            return None
        finally:
            connection.close()

        if not body or len(body) > VIEWER_ASSET_MAX_BYTES:
            return None

        compressed = None
        if urlparse(upstream_path).path.endswith(COMPRESSIBLE_SUFFIXES):
            compressed = gzip.compress(body, compresslevel=6)
            if len(compressed) >= len(body):
                compressed = None

        return {
            "content_type": content_type,
            "raw": body,
            "gzip": compressed,
            "etag": f'"{hashlib.sha256(body).hexdigest()[:16]}"',
        }


runtime = NewtonRuntime()
viewer_assets = ViewerAssetCache()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _relay(self, host: str, port: int, upstream_path: str) -> None:
        """Reverse-proxy the current request to an upstream HTTP/1.1 service.

        The Rerun web viewer (HTTP/1.1) and its gRPC message proxy (gRPC-Web
        over HTTP/1.1) are streamed through this launcher so the whole viewer is
        same-origin and only one port needs to be exposed.
        """
        self.close_connection = True
        is_grpc_stream = port == GRPC_PORT
        relay_started = time.perf_counter()
        window_started = relay_started
        window_bytes = 0
        max_client_write_seconds = 0.0
        if is_grpc_stream:
            # region agent log
            debug_log(
                "H4",
                "server.py:Handler._relay:start",
                "gRPC-Web relay opened",
                {"method": self.command, "path": upstream_path, "client": self.client_address[0]},
            )
            # endregion
        try:
            upstream = socket.create_connection((host, port))
        except OSError as exc:
            try:
                self.send_error(HTTPStatus.BAD_GATEWAY, f"Viewer upstream unavailable: {exc}")
            except OSError:
                pass
            return

        try:
            header_lines = [f"{self.command} {upstream_path} HTTP/1.1\r\n"]
            for key, value in self.headers.items():
                if key.lower() in ("host", "connection", "keep-alive", "proxy-connection"):
                    continue
                header_lines.append(f"{key}: {value}\r\n")
            header_lines.append(f"Host: {host}:{port}\r\n")
            header_lines.append("Connection: close\r\n\r\n")
            upstream.sendall("".join(header_lines).encode("latin-1"))

            content_length = self.headers.get("Content-Length")
            if content_length is not None:
                remaining = int(content_length)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    upstream.sendall(chunk)
                    remaining -= len(chunk)

            while True:
                data = upstream.recv(65536)
                if not data:
                    break
                client_write_started = time.perf_counter()
                self.wfile.write(data)
                self.wfile.flush()
                client_write_seconds = time.perf_counter() - client_write_started
                if is_grpc_stream:
                    window_bytes += len(data)
                    max_client_write_seconds = max(max_client_write_seconds, client_write_seconds)
                    window_elapsed = time.perf_counter() - window_started
                    if window_elapsed >= 5.0:
                        # region agent log
                        debug_log(
                            "H4",
                            "server.py:Handler._relay:window",
                            "Five-second gRPC-Web relay window",
                            {
                                "bytes": window_bytes,
                                "elapsed_s": round(window_elapsed, 4),
                                "throughput_mbps": round(window_bytes * 8 / window_elapsed / 1_000_000, 3),
                                "max_client_write_ms": round(max_client_write_seconds * 1000, 3),
                            },
                        )
                        # endregion
                        window_started = time.perf_counter()
                        window_bytes = 0
                        max_client_write_seconds = 0.0
        except OSError:
            pass
        finally:
            upstream.close()
            if is_grpc_stream:
                # region agent log
                debug_log(
                    "H4",
                    "server.py:Handler._relay:end",
                    "gRPC-Web relay closed",
                    {"duration_s": round(time.perf_counter() - relay_started, 4)},
                )
                # endregion

    def _serve_viewer_asset(self, upstream_path: str) -> bool:
        entry = viewer_assets.get(upstream_path)
        if entry is None:
            return False

        cache_control = f"public, max-age={VIEWER_ASSET_MAX_AGE}"
        if self.headers.get("If-None-Match") == entry["etag"]:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", entry["etag"])
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            return True

        compressed = entry["gzip"] is not None and "gzip" in self.headers.get("Accept-Encoding", "")
        body = entry["gzip"] if compressed else entry["raw"]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", entry["content_type"])
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", entry["etag"])
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return True

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/viewer" or path.startswith("/viewer/"):
            upstream_path = self.path[len("/viewer"):] or "/"
            if path.endswith(VIEWER_ASSET_SUFFIXES) and self._serve_viewer_asset(upstream_path):
                return
            self._relay(*WEB_UPSTREAM, upstream_path)
            return
        if path == "/api/status":
            self._send_json(runtime.status())
            return
        if path == "/api/log":
            self._send_json({"lines": runtime.tail_log()})
            return
        # A cached index.html paired with a freshly edited app.js breaks the
        # frontend, so the launcher's own files are always served uncached.
        self._no_store = True
        super().do_GET()

    def end_headers(self) -> None:
        if getattr(self, "_no_store", False):
            self._no_store = False
            self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def send_head(self):
        # SimpleHTTPRequestHandler answers 304 from If-Modified-Since before any
        # cache directive is consulted, so a browser holding a stale copy would
        # keep it. Ignoring the validator makes every reply a full response.
        del self.headers["If-Modified-Since"]
        return super().send_head()

    def do_POST(self) -> None:
        if self.headers.get("Content-Type", "").startswith("application/grpc"):
            self._relay(*GRPC_UPSTREAM, self.path)
            return
        path = urlparse(self.path).path
        if path == "/api/run":
            content_length = min(int(self.headers.get("Content-Length", "0")), 4096)
            try:
                payload = json.loads(self.rfile.read(content_length) or b"{}")
                status = runtime.start(str(payload.get("example", "")))
                self._send_json(status, HTTPStatus.ACCEPTED)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if path == "/api/stop":
            runtime.stop()
            self._send_json(runtime.status())
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:
        print(f"[launcher] {self.address_string()} {format % args}", flush=True)


def main() -> None:
    runtime.start("basic_shapes")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Newton launcher: http://{HOST}:{PORT}", flush=True)
    print(f"Rerun viewer: http://{HOST}:{VIEWER_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.stop()


if __name__ == "__main__":
    main()
