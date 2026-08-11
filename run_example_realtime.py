#!/usr/bin/env python3
"""Run a Newton example at a browser-friendly frame rate without opening tabs."""

from __future__ import annotations

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


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: run_example_realtime.py <example_name> [example options]")
    emit_stage("boot")
    disable_viewer_popups()
    newton.examples.run = paced_run
    newton.examples.main()


if __name__ == "__main__":
    main()
