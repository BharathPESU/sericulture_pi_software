"""Unit tests for camera, tracking, and movement analysis."""

import pytest
import time
from sericulture.config import CameraConfig, MovementConfig, TrackingConfig
from sericulture.hardware.camera_uvc import MockCameraBackend, UVCCamera
from sericulture.vision.hailo_inference import Detection
from sericulture.vision.movement_analysis import MovementAnalyzer
from sericulture.vision.tracking import SilkwormTracker


def test_camera_frame_freshness():
    cfg = CameraConfig(frame_freshness_timeout_s=0.1)
    backend = MockCameraBackend()
    camera = UVCCamera(cfg, backend=backend)
    assert camera.open() is True

    ok, frame, meta = camera.read_frame()
    assert ok is True
    assert camera.is_frame_fresh() is True

    time.sleep(0.12)
    assert camera.is_frame_fresh() is False


def test_tracker_maintains_identity():
    cfg = TrackingConfig(iou_threshold=0.2, min_hits=1)
    tracker = SilkwormTracker(cfg)

    # Frame 1: Detection at [0.1, 0.1, 0.2, 0.2]
    det1 = [Detection(bbox_xyxy=(0.1, 0.1, 0.2, 0.2), confidence=0.9, class_id=0, class_name="silkworm")]
    tracks1 = tracker.update(det1, timestamp_s=1.0)
    assert len(tracks1) == 1
    t_id = tracks1[0].track_id

    # Frame 2: Slight movement to [0.11, 0.11, 0.21, 0.21]
    det2 = [Detection(bbox_xyxy=(0.11, 0.11, 0.21, 0.21), confidence=0.9, class_id=0, class_name="silkworm")]
    tracks2 = tracker.update(det2, timestamp_s=2.0)
    assert len(tracks2) == 1
    assert tracks2[0].track_id == t_id  # Track ID preserved!


def test_movement_analyzer_suspends_when_camera_not_fresh():
    cfg = MovementConfig()
    analyzer = MovementAnalyzer(cfg)

    # Calling analyze with camera_fresh=False must suspend analysis
    res = analyzer.analyze(tracks=[], camera_fresh=False)
    assert res.is_valid is False
    assert "suspended" in res.reason.lower()
