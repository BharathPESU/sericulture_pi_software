"""Vision pipeline coordinating capture, Hailo inference, tracking, and movement analysis.

SAFETY RULES:
  - Runs with bounded execution time; exceptions are caught and logged.
  - Vision degradation or crashes NEVER block actuator safety or mains response.
  - If camera or Hailo fails, vision is flagged unhealthy and decisions requiring AI are blocked.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Any, Callable

from sericulture.hardware.camera_uvc import UVCCamera
from sericulture.vision.hailo_inference import Detection, HailoInferenceManager
from sericulture.vision.movement_analysis import MovementAnalysisResult, MovementAnalyzer
from sericulture.vision.tracking import SilkwormTracker, TrackedObject

logger = logging.getLogger(__name__)


@dataclass
class VisionCycleResult:
    """Summary of a single execution of the vision pipeline."""
    success: bool
    detections: list[Detection]
    tracks: list[TrackedObject]
    movement: MovementAnalysisResult | None
    latency_ms: float
    error_message: str = ""


class VisionPipeline:
    """Coordinates camera capture, Hailo-8 inference, tracking, and movement analysis."""

    def __init__(
        self,
        camera: UVCCamera,
        hailo: HailoInferenceManager,
        tracker: SilkwormTracker,
        movement_analyzer: MovementAnalyzer,
    ):
        self._camera = camera
        self._hailo = hailo
        self._tracker = tracker
        self._analyzer = movement_analyzer

        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

        self._fps: float = 0.0
        self._frame_count: int = 0
        self._last_fps_calc_t: float = time.monotonic()
        self._last_result: VisionCycleResult | None = None
        self._is_healthy: bool = True

        self._callbacks: list[Callable[[VisionCycleResult], None]] = []

    @property
    def is_healthy(self) -> bool:
        return self._is_healthy

    @property
    def current_fps(self) -> float:
        return self._fps

    @property
    def last_result(self) -> VisionCycleResult | None:
        return self._last_result

    def add_result_listener(self, cb: Callable[[VisionCycleResult], None]) -> None:
        self._callbacks.append(cb)

    def process_one_frame(self) -> VisionCycleResult:
        """Execute one complete vision cycle synchronously."""
        start_t = time.monotonic()
        try:
            # 1. Capture frame
            ok, frame, meta = self._camera.read_frame()
            if not ok or frame is None or meta is None:
                self._is_healthy = False
                res = VisionCycleResult(
                    success=False,
                    detections=[],
                    tracks=[],
                    movement=None,
                    latency_ms=(time.monotonic() - start_t) * 1000.0,
                    error_message="Camera read failed",
                )
                self._last_result = res
                return res

            # 2. Hailo inference
            detections = self._hailo.run_inference(frame)

            # 3. Multi-object tracking
            tracks = self._tracker.update(detections, timestamp_s=meta.monotonic_timestamp_s)

            # 4. Movement & inactivity analysis
            movement = self._analyzer.analyze(tracks, camera_fresh=meta.is_fresh)

            # 5. Metrics calculation
            now = time.monotonic()
            self._frame_count += 1
            dt = now - self._last_fps_calc_t
            if dt >= 2.0:
                self._fps = round(self._frame_count / dt, 1)
                self._frame_count = 0
                self._last_fps_calc_t = now

            self._is_healthy = True
            res = VisionCycleResult(
                success=True,
                detections=detections,
                tracks=tracks,
                movement=movement,
                latency_ms=(now - start_t) * 1000.0,
            )
            self._last_result = res

            for cb in self._callbacks:
                try:
                    cb(res)
                except Exception as cb_err:
                    logger.error(f"Error in vision callback: {cb_err}")

            return res

        except Exception as e:
            self._is_healthy = False
            logger.error(f"Unhandled error in vision pipeline: {e}")
            res = VisionCycleResult(
                success=False,
                detections=[],
                tracks=[],
                movement=None,
                latency_ms=(time.monotonic() - start_t) * 1000.0,
                error_message=str(e),
            )
            self._last_result = res
            return res

    def start_worker(self, target_interval_s: float = 0.1) -> None:
        """Start background vision processing worker thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(
                target=self._run_loop,
                args=(target_interval_s,),
                name="VisionWorker",
                daemon=True,
            )
            self._thread.start()

    def _run_loop(self, interval_s: float) -> None:
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            self.process_one_frame()
            elapsed = time.monotonic() - t0
            sleep_t = max(0.01, interval_s - elapsed)
            time.sleep(sleep_t)

    def stop_worker(self) -> None:
        """Signal worker to stop and wait for completion."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
