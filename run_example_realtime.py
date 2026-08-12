#!/usr/bin/env python3
"""Run a Newton example at a browser-friendly frame rate without opening tabs."""

from __future__ import annotations

import os
import sys
import time

import rerun as rr
import warp as wp

import newton.examples

# Milestone markers the launcher parses out of this process's stdout to report
# real load progress. Newton itself reports no percentage, so these coarse
# transitions are the only truthful progress signal available.
STAGE_MARKER = "NEWTON_LAUNCHER_STAGE:"


def emit_stage(stage: str) -> None:
    print(f"{STAGE_MARKER}{stage}", flush=True)


def paced_run(example, args) -> None:
    emit_stage("scene")
    if hasattr(example, "gui") and hasattr(example.viewer, "register_ui_callback"):
        example.viewer.register_ui_callback(lambda ui: example.gui(ui), position="side")

    frames = 0
    while example.viewer.is_running():
        frame_started = time.perf_counter()
        if not example.viewer.is_paused():
            with wp.ScopedTimer("step", active=False):
                example.step()
        with wp.ScopedTimer("render", active=False):
            example.render()
        frames += 1
        if frames == 1:
            emit_stage("streaming")

        simulation_dt = float(getattr(example, "frame_dt", 1.0 / 30.0))
        target_period = max(simulation_dt, 1.0 / 30.0)
        remaining = target_period - (time.perf_counter() - frame_started)
        if remaining > 0:
            time.sleep(remaining)

    example.viewer.close()


def disable_viewer_popups() -> None:
    serve_web_viewer = rr.serve_web_viewer

    def serve_web_viewer_embedded(*args, **kwargs):
        kwargs["open_browser"] = False
        return serve_web_viewer(*args, **kwargs)

    rr.serve_web_viewer = serve_web_viewer_embedded


def limit_stream_buffer() -> None:
    """Cap the gRPC replay buffer.

    Rerun's gRPC server buffers log data so late-connecting viewers receive
    history, defaulting to 1 GiB. A browser that connects minutes into a run
    must work through that backlog before it reaches live frames, and the
    viewer's reported latency is roughly the buffer depth expressed in seconds.
    Geometry is logged as static data, which is never dropped, so a small
    buffer still yields a complete scene.
    """
    limit = os.environ.get("RERUN_SERVER_MEMORY_LIMIT", "4MiB")
    serve_grpc = rr.serve_grpc

    def serve_grpc_bounded(*args, **kwargs):
        kwargs.setdefault("server_memory_limit", limit)
        return serve_grpc(*args, **kwargs)

    rr.serve_grpc = serve_grpc_bounded


def drop_stale_frames() -> None:
    """Log body poses as temporal rather than static data.

    Newton defaults to static poses to bound the browser's memory. Static data
    is exempt from the gRPC server's buffer limit, so every pose ever logged is
    replayed to a connecting viewer, which then grinds through minutes of
    history before it reaches the live frame. Temporal poses fall under the
    buffer limit and are dropped once stale, so a viewer joins near-live.
    """
    from newton.viewer import ViewerRerun

    init = ViewerRerun.__init__

    def init_streaming(self, *args, **kwargs):
        kwargs["keep_historical_data"] = True
        init(self, *args, **kwargs)

    ViewerRerun.__init__ = init_streaming


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: run_example_realtime.py <example_name> [example options]")
    emit_stage("boot")
    disable_viewer_popups()
    limit_stream_buffer()
    drop_stale_frames()
    newton.examples.run = paced_run
    newton.examples.main()


if __name__ == "__main__":
    main()
