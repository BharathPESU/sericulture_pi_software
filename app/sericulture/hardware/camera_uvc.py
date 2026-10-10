"""USB UVC camera driver with stable identity discovery and freshness checking.

Targets Arducam OV2710 2MP USB camera. Discovers camera via udev paths
(/dev/v4l/by-id/, /dev/v4l/by-path/) rather than hardcoding /dev/video0.

SAFETY RULES:
  - If camera is disconnected or frames are stale, vision is marked UNAVAILABLE.
  - Camera absence/errors MUST NEVER be interpreted as silkworm inactivity.
  - Camera errors must not crash the supervisor or safety control loops.
"""

from __future__ import annotations

import glob
import logging
import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

from sericulture.config import CameraConfig
from sericulture.event_types import (
    ComponentId,
    DiagnosticResult,
    DiagnosticStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class FrameMetadata:
    """Metadata attached to an acquired video frame."""
    frame_number: int
    monotonic_timestamp_s: float
    width: int
    height: int
    channels: int
    is_fresh: bool


class CameraBackend(Protocol):
    """Protocol for video capture backends."""
    def open(self, path: str, width: int, height: int, fps: int) -> bool:
        ...

    def is_opened(self) -> bool:
        ...

    def read(self) -> tuple[bool, Any]:
        """Read frame. Returns (success, frame_array)."""
        ...

    def release(self) -> None:
        ...


class MockCameraBackend:
    """Mock camera backend for testing without physical camera hardware."""

    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height
        self._opened = False
        self.fail_read = False
        self.fail_open = False
        self.frame_count = 0

    def open(self, path: str, width: int, height: int, fps: int) -> bool:
        if self.fail_open:
            self._opened = False
            return False
        self._opened = True
        return True

    def is_opened(self) -> bool:
        return self._opened

    def read(self) -> tuple[bool, Any]:
        if not self._opened or self.fail_read:
            return False, None
        self.frame_count += 1
        # Create a lightweight dummy frame representation (mock object with shape)
        try:
            import numpy as np
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        except ImportError:
            # Fallback if numpy is not installed in test environment
            class DummyFrame:
                shape = (self.height, self.width, 3)
            frame = DummyFrame()
        return True, frame

    def release(self) -> None:
        self._opened = False


class OpenCVCameraBackend:
    """Real V4L2/UVC backend using OpenCV / GStreamer."""

    def __init__(self):
        self._cap: Any = None

    def open(self, path: str, width: int, height: int, fps: int) -> bool:
        try:
            import cv2
            # Try V4L2 backend
            self._cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
            if not self._cap.isOpened():
                self._cap = cv2.VideoCapture(path)

            if self._cap.isOpened():
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self._cap.set(cv2.CAP_PROP_FPS, fps)
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to open camera via OpenCV: {e}")
            return False

    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def read(self) -> tuple[bool, Any]:
        if not self.is_opened():
            return False, None
        return self._cap.read()

    def release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None


class UVCCamera:
    """High-level UVC camera manager with discovery, health tracking, and frame validation."""

    def __init__(
        self,
        config: CameraConfig,
        backend: CameraBackend | None = None,
    ):
        self._config = config
        self._backend = backend or MockCameraBackend(config.width, config.height)
        self._lock = Lock()
        self._device_path: str = ""
        self._last_frame: Any = None
        self._last_frame_time: float = 0.0
        self._frame_count: int = 0
        self._consecutive_errors: int = 0
        self._is_connected: bool = False

    @property
    def component_id(self) -> ComponentId:
        return ComponentId.CAMERA

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def discover_device(self) -> str | None:
        """Find the UVC camera device path using stable identities or video nodes."""
        # 1. Configured explicit path
        if self._config.device_path and os.path.exists(self._config.device_path):
            return self._config.device_path

        # 2. Look in /dev/v4l/by-id/ for specific serial or OV2710
        if os.path.exists("/dev/v4l/by-id"):
            devices = glob.glob("/dev/v4l/by-id/*")
            if self._config.serial_number:
                for dev in devices:
                    if self._config.serial_number in dev:
                        return os.path.realpath(dev)
            for dev in devices:
                if "ov2710" in dev.lower() or "arducam" in dev.lower() or "camera" in dev.lower():
                    return os.path.realpath(dev)
            if devices:
                return os.path.realpath(devices[0])

        # 3. Fallback scan /dev/video*
        video_nodes = sorted(glob.glob("/dev/video*"))
        if video_nodes:
            # Pick first available node
            return video_nodes[0]

        return None

    def open(self) -> bool:
        """Discover and open the camera device."""
        with self._lock:
            if self._backend.is_opened():
                self._is_connected = True
                return True

            device = self.discover_device()
            if not device and not isinstance(self._backend, MockCameraBackend):
                logger.warning("No camera video device found during discovery.")
                self._is_connected = False
                return False

            self._device_path = device or "/dev/video0"
            success = self._backend.open(
                self._device_path,
                self._config.width,
                self._config.height,
                self._config.fps,
            )
            self._is_connected = success
            if success:
                logger.info(f"Camera opened successfully on {self._device_path} ({self._config.width}x{self._config.height} @ {self._config.fps}fps)")
            else:
                logger.warning(f"Failed to open camera on {self._device_path}")
            return success

    def read_frame(self) -> tuple[bool, Any, FrameMetadata | None]:
        """Read a single frame with freshness tracking and error counting."""
        with self._lock:
            if not self._backend.is_opened():
                self._is_connected = False
                return False, None, None

            ret, frame = self._backend.read()
            now = time.monotonic()

            if not ret or frame is None:
                self._consecutive_errors += 1
                logger.warning(f"Camera frame capture failed (consecutive: {self._consecutive_errors})")
                if self._consecutive_errors >= 5:
                    self._is_connected = False
                return False, None, None

            # Frame read succeeded
            self._consecutive_errors = 0
            self._frame_count += 1
            self._last_frame = frame
            self._last_frame_time = now

            shape = getattr(frame, "shape", (self._config.height, self._config.width, 3))
            h = shape[0] if len(shape) > 0 else self._config.height
            w = shape[1] if len(shape) > 1 else self._config.width
            c = shape[2] if len(shape) > 2 else 3

            meta = FrameMetadata(
                frame_number=self._frame_count,
                monotonic_timestamp_s=now,
                width=w,
                height=h,
                channels=c,
                is_fresh=True,
            )
            return True, frame, meta

    def is_frame_fresh(self, timeout_s: float | None = None) -> bool:
        """Check if the most recent acquired frame is within freshness timeout."""
        with self._lock:
            if self._last_frame is None or self._last_frame_time == 0.0:
                return False
            limit = timeout_s or self._config.frame_freshness_timeout_s
            return (time.monotonic() - self._last_frame_time) <= limit

    def check(self) -> DiagnosticResult:
        """Diagnostic check: discover, open, read test frame, validate shape and freshness."""
        dev = self.discover_device()
        if not dev and not isinstance(self._backend, MockCameraBackend):
            return DiagnosticResult(
                component=ComponentId.CAMERA,
                status=DiagnosticStatus.MISSING,
                message="Camera device node not found on USB/V4L2.",
            )

        # Attempt to open and capture a test frame
        was_open = self._backend.is_opened()
        if not was_open:
            if not self.open():
                return DiagnosticResult(
                    component=ComponentId.CAMERA,
                    status=DiagnosticStatus.FAIL,
                    message=f"Failed to open camera on {self._device_path or dev}",
                )

        ret, frame, meta = self.read_frame()
        if not ret or frame is None or meta is None:
            return DiagnosticResult(
                component=ComponentId.CAMERA,
                status=DiagnosticStatus.FAIL,
                message="Camera opened but failed to capture a test frame.",
            )

        if not self.is_frame_fresh():
            return DiagnosticResult(
                component=ComponentId.CAMERA,
                status=DiagnosticStatus.FAIL,
                message="Camera frame is stale.",
            )

        return DiagnosticResult(
            component=ComponentId.CAMERA,
            status=DiagnosticStatus.PASS,
            message=f"Camera healthy on {self._device_path}: {meta.width}x{meta.height} frame captured.",
            detail={
                "device_path": self._device_path,
                "width": meta.width,
                "height": meta.height,
                "frame_count": meta.frame_number,
            },
        )

    def close(self) -> None:
        """Release camera resources."""
        with self._lock:
            self._backend.release()
            self._is_connected = False
            self._last_frame = None
