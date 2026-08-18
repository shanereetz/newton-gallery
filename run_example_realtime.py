#!/usr/bin/env python3
"""Run a Newton example at a browser-friendly frame rate without opening tabs."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import rerun as rr
import warp as wp

import newton.examples

# Milestone markers the launcher parses out of this process's stdout to report
# real load progress. Newton itself reports no percentage, so these coarse
# transitions are the only truthful progress signal available.
STAGE_MARKER = "NEWTON_LAUNCHER_STAGE:"
DEBUG_LOG_PATH = Path("/home/sreetz/Desktop/newton-gallery/.cursor/debug-72d3bf.log")
DEBUG_RUN_ID = f"{os.getpid()}-{int(time.time())}"

# Frames logged to the viewer per second. One simulation step per logged frame
# keeps MuJoCo contact budgets stable; the stream stays bounded at this rate.
RENDER_FPS = 30.0


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


def emit_stage(stage: str) -> None:
    print(f"{STAGE_MARKER}{stage}", flush=True)


def paced_run(example, args) -> None:
    emit_stage("scene")
    if hasattr(example, "gui") and hasattr(example.viewer, "register_ui_callback"):
        example.viewer.register_ui_callback(lambda ui: example.gui(ui), position="side")

    # Two independent clocks. Physics advances at the example's own frame_dt so
    # the scene runs at real time. Publishing to the viewer is throttled to
    # PUBLISH_FPS: a Newton frame is only logged when a publish slot is due,
    # which is what bounds the stream. MPM emits several MB per logged frame, so
    # publishing every physics frame saturates a remote link and the viewer's
    # backpressure stalls the whole loop; decoupling caps the bytes/sec while
    # physics keeps stepping.
    frame_dt = float(getattr(example, "frame_dt", 1.0 / 60.0))
    publish_fps = float(os.environ.get("NEWTON_PUBLISH_FPS", str(RENDER_FPS)))
    publish_period = 1.0 / publish_fps if publish_fps > 0 else 0.0
    # Publishing is scheduled in whole physics steps, not on the wall clock: a
    # step is the smallest unit the loop can interrupt, so a wall-clock period
    # gets rounded up to the next step boundary and the effective rate lands
    # below the target (33.3 ms against a 10 ms step published at 40 ms = 25 fps).
    # Flooring the step count rounds the other way, so the rate is always at or
    # above publish_fps.
    steps_per_publish = max(1, int(publish_period / frame_dt)) if publish_period > 0 else 1

    # region agent log
    debug_log(
        "H4,H6",
        "run_example_realtime.py:paced_run:start",
        "Runner timing configuration",
        {
            "example": sys.argv[1] if len(sys.argv) > 1 else None,
            "frame_dt": frame_dt,
            "publish_fps": publish_fps,
            "steps_per_publish": steps_per_publish,
            "point_max": os.environ.get("NEWTON_POINT_MAX", "150000"),
            "keep_historical_data": getattr(example.viewer, "keep_historical_data", None),
            "server_memory_limit": os.environ.get("RERUN_SERVER_MEMORY_LIMIT"),
        },
    )
    # endregion

    steps = 0
    publishes = 0
    emitted_streaming = False
    last_publish = 0.0
    window_started = time.perf_counter()
    step_seconds = 0.0
    render_seconds = 0.0
    max_step_seconds = 0.0
    max_render_seconds = 0.0
    steps_since_publish = 0
    while example.viewer.is_running():
        frame_started = time.perf_counter()
        paused = example.viewer.is_paused()
        if not paused:
            step_started = time.perf_counter()
            with wp.ScopedTimer("step", active=False):
                example.step()
            step_duration = time.perf_counter() - step_started
            step_seconds += step_duration
            max_step_seconds = max(max_step_seconds, step_duration)
            steps += 1
            steps_since_publish += 1

        now = time.perf_counter()
        # While paused no steps accumulate, so fall back to the wall clock to keep
        # the viewer fed.
        due = steps_since_publish >= steps_per_publish
        if paused and publish_period > 0.0:
            due = now - last_publish >= publish_period
        if publish_period <= 0.0 or due:
            last_publish = now
            steps_since_publish = 0
            render_started = time.perf_counter()
            with wp.ScopedTimer("render", active=False):
                example.render()
            render_duration = time.perf_counter() - render_started
            render_seconds += render_duration
            max_render_seconds = max(max_render_seconds, render_duration)
            publishes += 1
            if not emitted_streaming:
                emit_stage("streaming")
                emitted_streaming = True

        window_elapsed = time.perf_counter() - window_started
        if window_elapsed >= 5.0:
            # region agent log
            debug_log(
                "H4,H6",
                "run_example_realtime.py:paced_run:window",
                "Five-second runner timing window",
                {
                    "steps": steps,
                    "publishes": publishes,
                    "elapsed_s": round(window_elapsed, 4),
                    "step_hz": round(steps / window_elapsed, 3),
                    "publish_fps": round(publishes / window_elapsed, 3),
                    "avg_step_ms": round(step_seconds * 1000 / max(steps, 1), 3),
                    "max_step_ms": round(max_step_seconds * 1000, 3),
                    "avg_render_ms": round(render_seconds * 1000 / max(publishes, 1), 3),
                    "max_render_ms": round(max_render_seconds * 1000, 3),
                },
            )
            # endregion
            steps = 0
            publishes = 0
            window_started = time.perf_counter()
            step_seconds = 0.0
            render_seconds = 0.0
            max_step_seconds = 0.0
            max_render_seconds = 0.0

        remaining = frame_dt - (time.perf_counter() - frame_started)
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


def slim_point_clouds() -> None:
    """Trim what point clouds put on the wire without touching the simulation.

    MPM clouds re-send a full per-particle color and radius every frame even
    though every particle shares one value (confirmed: 1 unique row across 453k
    particles), which is 3.6 MB/frame of redundancy. Uniform attributes collapse
    to a single value that Rerun broadcasts. Positions are the remaining cost, so
    the streamed cloud is capped to NEWTON_POINT_MAX points by uniform stride;
    physics still runs at full resolution, only the viewer payload shrinks, which
    keeps 30 fps within a remote link's bandwidth.
    """
    import numpy as np
    import pyarrow as pa

    log = rr.log
    max_points = int(os.environ.get("NEWTON_POINT_MAX", "150000"))

    def component_rows(component, count):
        """Return the component as a (count, width) numeric array.

        Vector components arrive as arrow fixed_size_list, whose to_numpy() gives
        an object array of per-point arrays; flattening first yields the packed
        numeric buffer. Scalar components (radii float, colors packed uint32)
        convert directly.
        """
        try:
            arrow = component.as_arrow_array()
            if pa.types.is_fixed_size_list(arrow.type):
                width = arrow.type.list_size
                arr = np.asarray(arrow.flatten().to_numpy(zero_copy_only=False))
            else:
                width = 1
                arr = np.asarray(arrow.to_numpy(zero_copy_only=False))
        except Exception:
            return None
        if arr.dtype == object or arr.size != count * width:
            return None
        return arr.reshape(count, width)

    def slim(item):
        pos_comp = getattr(item, "positions", None)
        if pos_comp is None:
            return None
        try:
            count = len(pos_comp.as_arrow_array())
        except Exception:
            return None
        if count <= 1000:
            return None
        positions = component_rows(pos_comp, count)
        if positions is None or positions.shape[1] != 3:
            return None

        if max_points > 0 and count > max_points:
            stride = (count + max_points - 1) // max_points
            index = slice(None, None, stride)
        else:
            index = slice(None)

        rebuilt = {"positions": positions[index]}
        for name in ("colors", "radii"):
            comp = getattr(item, name, None)
            if comp is None:
                continue
            arr = component_rows(comp, count)
            if arr is None:
                continue
            arr = arr[index]
            if arr.shape[0] > 0 and bool((arr == arr[:1]).all()):
                arr = arr[:1]
            if arr.shape[1] == 1:
                arr = arr.reshape(-1)
            rebuilt[name] = arr
        return rr.Points3D(**rebuilt)

    def log_slim(entity_path, *payload, **kwargs):
        new_payload = []
        for item in payload:
            replacement = slim(item) if type(item).__name__ == "Points3D" else None
            new_payload.append(replacement if replacement is not None else item)
        return log(entity_path, *new_payload, **kwargs)

    rr.log = log_slim


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


# region agent log
def instrument_log_bytes() -> None:
    """Attribute wire bytes per entity path to find what fills a published frame."""
    import numpy as np

    log = rr.log
    totals: dict[tuple[str, str, bool], list[float]] = {}
    window_started = [time.perf_counter()]
    constancy_reported = [False]

    def report_constancy(entity_path, item):
        if constancy_reported[0]:
            return
        colors = getattr(item, "colors", None)
        radii = getattr(item, "radii", None)
        if colors is None and radii is None:
            return
        info = {"entity": str(entity_path)}
        for label, comp in (("colors", colors), ("radii", radii)):
            try:
                arr = np.asarray(comp.as_arrow_array().to_numpy(zero_copy_only=False))
            except Exception:
                continue
            uniq = np.unique(arr, axis=0) if arr.ndim > 1 else np.unique(arr)
            info[f"{label}_len"] = int(arr.shape[0])
            info[f"{label}_unique_rows"] = int(uniq.shape[0])
        constancy_reported[0] = True
        debug_log(
            "H11",
            "run_example_realtime.py:report_constancy",
            "Uniqueness of per-particle radii/colors on first Points3D",
            info,
        )

    def component_sizes(item):
        try:
            batches = list(item.as_component_batches())
        except Exception:
            return
        for batch in batches:
            try:
                arrow = batch.as_arrow_array()
            except Exception:
                continue
            try:
                name = str(batch.component_descriptor())
            except Exception:
                name = type(batch).__name__
            yield name, int(getattr(arrow, "nbytes", 0) or 0), len(arrow)

    def log_measured(entity_path, *payload, static=False, **kwargs):
        for item in payload:
            if type(item).__name__ == "Points3D":
                report_constancy(entity_path, item)
            for name, size, count in component_sizes(item):
                key = (str(entity_path), name, bool(static))
                entry = totals.setdefault(key, [0.0, 0.0, 0.0])
                entry[0] += size
                entry[1] += 1
                entry[2] = count
        elapsed = time.perf_counter() - window_started[0]
        if elapsed >= 5.0:
            ranked = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)
            debug_log(
                "H7,H8,H9,H10",
                "run_example_realtime.py:instrument_log_bytes",
                "Five-second per-entity log byte window",
                {
                    "elapsed_s": round(elapsed, 3),
                    "total_mb": round(sum(v[0] for _, v in ranked) / 1e6, 3),
                    "total_mbps": round(sum(v[0] for _, v in ranked) * 8 / elapsed / 1e6, 1),
                    "top": [
                        {
                            "entity": k[0],
                            "component": k[1],
                            "static": k[2],
                            "mb": round(v[0] / 1e6, 3),
                            "logs": int(v[1]),
                            "elements": int(v[2]),
                        }
                        for k, v in ranked[:8]
                    ],
                    "distinct_entities": len({k[0] for k in totals}),
                },
            )
            totals.clear()
            window_started[0] = time.perf_counter()
        return log(entity_path, *payload, static=static, **kwargs)

    rr.log = log_measured


# endregion


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
    # region agent log
    if os.environ.get("NEWTON_MEASURE_LOG_BYTES") == "1":
        instrument_log_bytes()
    # endregion
    slim_point_clouds()
    newton.examples.run = paced_run
    newton.examples.main()


if __name__ == "__main__":
    main()
