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

# Frames logged to the viewer per second. One simulation step per logged frame
# keeps MuJoCo contact budgets stable; the stream stays bounded at this rate.
RENDER_FPS = 30.0


def emit_stage(stage: str) -> None:
    print(f"{STAGE_MARKER}{stage}", flush=True)


def paced_run(example, args) -> None:
    emit_stage("scene")
    if hasattr(example, "gui") and hasattr(example.viewer, "register_ui_callback"):
        example.viewer.register_ui_callback(lambda ui: example.gui(ui), position="side")

    # Log at most RENDER_FPS frames/sec. Step at most once per logged frame —
    # catching up with multi-step bursts blows MuJoCo contact budgets (nefc /
    # njmax overflow) and collapses articulated scenes. Prefer slight slow-mo
    # under load over an unstable simulation.
    frame_dt = float(getattr(example, "frame_dt", 1.0 / 60.0))
    render_period = max(frame_dt, 1.0 / RENDER_FPS)

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

    Newton logs instanced meshes non-static (log_instances) and a plain mesh
    non-static on its first appearance, so with a small replay buffer the
    geometry is dropped within a minute and a browser that connects later
    renders an empty viewport. Forcing every Mesh3D static exempts it from the
    buffer limit; each re-log of a mesh overwrites the last, so the cost is a
    fixed amount of memory per mesh.
    """
    log = rr.log

    def log_static_geometry(entity_path, *payload, static=False, **kwargs):
        if any(isinstance(item, rr.Mesh3D) for item in payload):
            static = True
        return log(entity_path, *payload, static=static, **kwargs)

    rr.log = log_static_geometry


def stream_recent_poses() -> None:
    """Keep body poses in the timeline so a late viewer sees the current frame.

    With Newton's default (keep_historical_data=False) poses are logged static
    and, paired with the bounded buffer, a browser attaching mid-run renders an
    empty viewport. keep_historical_data=True puts poses on the timeline, where
    the buffer keeps the most recent frames and drops the rest — bounded memory,
    and the viewer opens on live motion instead of nothing.
    """
    from newton.viewer import ViewerRerun

    init = ViewerRerun.__init__

    def init_streaming(self, *args, **kwargs):
        kwargs["keep_historical_data"] = True
        init(self, *args, **kwargs)

    ViewerRerun.__init__ = init_streaming


def normalize_instance_poses() -> None:
    """Normalize instance-pose quaternions before they reach Rerun.

    Newton emits quaternions straight from Warp transforms, and floating-point
    drift leaves some of them slightly off unit length. Rerun rejects any
    non-unit rotation as invalid and substitutes identity, which collapses
    articulated robots (ANYmal, Franka) into flat sheets. Normalizing here keeps
    every rotation valid; degenerate (zero / non-finite) quaternions fall back to
    identity xyzw.
    """
    import numpy as np

    orig = rr.InstancePoses3D

    def normalized(*args, **kwargs):
        q = kwargs.get("quaternions")
        if q is not None:
            arr = np.asarray(q, dtype=np.float64).reshape(-1, 4)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            bad = ~np.isfinite(norms[:, 0]) | (norms[:, 0] < 1e-8)
            safe = np.where(norms < 1e-8, 1.0, norms)
            arr = arr / safe
            arr[bad] = (0.0, 0.0, 0.0, 1.0)
            kwargs["quaternions"] = arr.astype(np.float32)
        return orig(*args, **kwargs)

    rr.InstancePoses3D = normalized


def clamp_ground_plane() -> None:
    """Bound the visual ground plane so auto-framing lands on the subject.

    Newton draws an "infinite" ground plane at 1.5x the world extents, which for
    the robot scenes is tens of meters across. Rerun frames every entity when the
    3D view first opens, so that plane sets the framing and a 1 m robot arrives a
    few pixels tall — the scene reads as broken. Clamping the plane keeps a
    ground reference without letting it dictate the camera.
    """
    from newton._src.viewer import viewer_rerun

    create_plane_mesh = viewer_rerun.create_plane_mesh
    limit = float(os.environ.get("NEWTON_GROUND_PLANE_MAX", "12.0"))

    def create_bounded_plane_mesh(width, length, *args, **kwargs):
        return create_plane_mesh(min(width, limit), min(length, limit), *args, **kwargs)

    viewer_rerun.create_plane_mesh = create_bounded_plane_mesh


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: run_example_realtime.py <example_name> [example options]")
    emit_stage("boot")
    disable_viewer_popups()
    limit_stream_buffer()
    pin_geometry()
    stream_recent_poses()
    normalize_instance_poses()
    clamp_ground_plane()
    newton.examples.run = paced_run
    newton.examples.main()


if __name__ == "__main__":
    main()
