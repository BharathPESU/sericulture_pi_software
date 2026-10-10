"""Multi-object tracker for silkworm monitoring.

Provides an IoU / ByteTrack tracker interface tracking individual silkworms
across frames with persistent track IDs and trajectory histories.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from sericulture.config import TrackingConfig
from sericulture.vision.hailo_inference import Detection

logger = logging.getLogger(__name__)


@dataclass
class TrackedObject:
    """An actively tracked silkworm instance."""
    track_id: int
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    hits: int
    age: int
    time_since_update: int
    centroid_history: list[tuple[float, float, float]] = field(default_factory=list)  # (cx, cy, timestamp_s)

    @property
    def current_centroid(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox_xyxy
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def calculate_iou(box1: tuple[float, float, float, float], box2: tuple[float, float, float, float]) -> float:
    """Calculate Intersection-over-Union between two boxes [x1, y1, x2, y2]."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter_area = inter_w * inter_h

    area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])
    union_area = area1 + area2 - inter_area

    if union_area <= 0.0:
        return 0.0
    return inter_area / union_area


class SilkwormTracker:
    """Multi-object tracker with IoU association and track lifecycle management."""

    def __init__(self, config: TrackingConfig):
        self._config = config
        self._next_id = 1
        self._tracks: dict[int, TrackedObject] = {}

    def update(self, detections: list[Detection], timestamp_s: float | None = None) -> list[TrackedObject]:
        """Update tracks with new frame detections.

        Matches detections to existing tracks using IoU matching.
        Creates new tracks for unmatched detections.
        Retires old tracks exceeding max_age.
        """
        now_s = timestamp_s if timestamp_s is not None else time.monotonic()

        # Increment age for existing tracks
        for track in self._tracks.values():
            track.age += 1
            track.time_since_update += 1

        unmatched_detections = list(range(len(detections)))
        matched_tracks: set[int] = set()

        # IoU greedy matching
        if self._tracks and detections:
            track_ids = list(self._tracks.keys())
            iou_matrix: list[list[float]] = []

            for tid in track_ids:
                track = self._tracks[tid]
                row = [calculate_iou(track.bbox_xyxy, det.bbox_xyxy) for det in detections]
                iou_matrix.append(row)

            # Match greedily highest IoU
            while True:
                best_iou = self._config.iou_threshold
                best_track_idx = -1
                best_det_idx = -1

                for t_idx, tid in enumerate(track_ids):
                    if tid in matched_tracks:
                        continue
                    for d_idx in unmatched_detections:
                        iou = iou_matrix[t_idx][d_idx]
                        if iou > best_iou:
                            best_iou = iou
                            best_track_idx = t_idx
                            best_det_idx = d_idx

                if best_track_idx == -1 or best_det_idx == -1:
                    break

                matched_tid = track_ids[best_track_idx]
                matched_tracks.add(matched_tid)
                unmatched_detections.remove(best_det_idx)

                # Update track
                det = detections[best_det_idx]
                track = self._tracks[matched_tid]
                track.bbox_xyxy = det.bbox_xyxy
                track.confidence = det.confidence
                track.hits += 1
                track.time_since_update = 0
                cx = (det.bbox_xyxy[0] + det.bbox_xyxy[2]) / 2.0
                cy = (det.bbox_xyxy[1] + det.bbox_xyxy[3]) / 2.0
                track.centroid_history.append((cx, cy, now_s))
                # Keep history bounded (last 100 points)
                if len(track.centroid_history) > 100:
                    track.centroid_history.pop(0)

        # Create new tracks for unmatched detections
        for d_idx in unmatched_detections:
            det = detections[d_idx]
            cx = (det.bbox_xyxy[0] + det.bbox_xyxy[2]) / 2.0
            cy = (det.bbox_xyxy[1] + det.bbox_xyxy[3]) / 2.0
            new_track = TrackedObject(
                track_id=self._next_id,
                bbox_xyxy=det.bbox_xyxy,
                confidence=det.confidence,
                hits=1,
                age=1,
                time_since_update=0,
                centroid_history=[(cx, cy, now_s)],
            )
            self._tracks[self._next_id] = new_track
            self._next_id += 1

        # Purge stale tracks exceeding max_age
        expired_ids = [
            tid for tid, track in self._tracks.items()
            if track.time_since_update > self._config.max_age
        ]
        for tid in expired_ids:
            del self._tracks[tid]

        # Return confirmed active tracks (hits >= min_hits)
        confirmed = [
            t for t in self._tracks.values()
            if t.hits >= self._config.min_hits and t.time_since_update == 0
        ]
        return confirmed

    def reset(self) -> None:
        """Clear all active tracks."""
        self._tracks.clear()
        self._next_id = 1
