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

# Frames logged to the viewer per second, and the ceiling on simulation steps per
# logged frame so a scene that cannot hold real time degrades instead of stalling.
RENDER_FPS = 30.0
MAX_STEPS_PER_FRAME = 16


def emit_stage(stage: str) -> None:
    print(f"{STAGE_MARKER}{stage}", flush=True)


def paced_run(example, args) -> None:
    emit_stage("scene")
    if hasattr(example, "gui") and hasattr(example.viewer, "register_ui_callback"):
        example.viewer.register_ui_callback(lambda ui: example.gui(ui), position="side")

    # Examples advance as little as 1/100 s per step, so stepping once per logged
    # frame plays the scene in slow motion (0.3x for a 100 Hz example). Step as
    # many times as the wall clock calls for, and log at most RENDER_FPS frames a
    # second so the browser stream stays bounded.
    frame_dt = float(getattr(example, "frame_dt", 1.0 / 60.0))
    render_period = max(frame_dt, 1.0 / RENDER_FPS)

    frames = 0
    sim_time = 0.0
    started = time.perf_counter()

    while example.viewer.is_running():
        frame_started = time.perf_counter()
        if example.viewer.is_paused():
            # Hold sim time against the wall clock so resuming does not sprint to
            # catch up on however long the pause lasted.
            started = frame_started - sim_time
        else:
            steps = 0
            with wp.ScopedTimer("step", active=False):
                while sim_time + frame_dt <= frame_started - started and steps < MAX_STEPS_PER_FRAME:
                    example.step()
                    sim_time += frame_dt
                    steps += 1
            if steps >= MAX_STEPS_PER_FRAME:
                # This scene cannot hold real time on this GPU. Give up the accrued
                # deficit instead of chasing it and falling further behind.
                started = frame_started - sim_time
        with wp.ScopedTimer("render", active=False):
            example.render()
        frames += 1
        if frames == 1:
            emit_stage("streaming")

        remaining = render_period - (time.perf_counter() - frame_started)
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
    must work through that backlog before it reaches live frames, which shows up
    as tens of seconds of reported latency, and the buffer is also what makes
    memory climb for the whole run. Only static data survives the limit, which
    is why `pin_geometry` has to run alongside this.
    """
    limit = os.environ.get("RERUN_SERVER_MEMORY_LIMIT", "4MiB")
    serve_grpc = rr.serve_grpc

    def serve_grpc_bounded(*args, **kwargs):
        kwargs.setdefault("server_memory_limit", limit)
        return serve_grpc(*args, **kwargs)

    rr.serve_grpc = serve_grpc_bounded


def pin_geometry() -> None:
    """Log meshes as static so a bounded buffer cannot evict the scene.

    Newton logs a mesh non-static the first time it sees its name, so with a
    small replay buffer the geometry is dropped within a minute and any browser
    that connects later renders an empty viewport. Static entries are exempt
    from the buffer limit, and each log of a given mesh overwrites the previous
    one, so pinning geometry costs a fixed amount of memory per mesh.
    """
    log = rr.log

    def log_static_geometry(entity_path, *payload, static=False, **kwargs):
        if any(isinstance(item, rr.Mesh3D) for item in payload):
            static = True
        return log(entity_path, *payload, static=static, **kwargs)

    rr.log = log_static_geometry


def stream_recent_poses() -> None:
    """Log body poses as temporal data so stale frames can be dropped.

    Newton defaults to static poses to bound the browser's memory, but static
    data is exempt from the server's buffer limit, so every pose ever logged is
    replayed to a connecting viewer and memory grows for the length of the run.
    Temporal poses fall under the limit; geometry stays put via `pin_geometry`.
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
    pin_geometry()
    stream_recent_poses()
    newton.examples.run = paced_run
    newton.examples.main()


if __name__ == "__main__":
    main()
