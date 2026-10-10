"""Silkworm movement and inactivity analyzer.

Analyzes trajectories of tracked silkworms to calculate activity scores and
detect prolonged inactivity (potential cocooning or health distress).

SAFETY RULES:
  - Camera disconnect or stale frames MUST NOT trigger an inactivity alert.
  - If vision is degraded or observations are insufficient, output is marked invalid.
  - Thresholds are treated as PROVISIONAL until validated by sericulture domain experts.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from sericulture.config import MovementConfig
from sericulture.vision.tracking import TrackedObject

logger = logging.getLogger(__name__)


@dataclass
class MovementAnalysisResult:
    """Result of analyzing tracked silkworm movements."""
    is_valid: bool
    tracked_count: int
    inactive_count: int
    avg_movement_score: float
    inactive_track_ids: list[int] = field(default_factory=list)
    reason: str = ""
    is_provisional: bool = True


class MovementAnalyzer:
    """Calculates movement velocity and detects sustained silkworm inactivity."""

    def __init__(self, config: MovementConfig):
        self._config = config

    def analyze(
        self,
        tracks: list[TrackedObject],
        camera_fresh: bool,
    ) -> MovementAnalysisResult:
        """Analyze tracks. Suspends output if camera is not fresh."""
        if not camera_fresh:
            return MovementAnalysisResult(
                is_valid=False,
                tracked_count=len(tracks),
                inactive_count=0,
                avg_movement_score=0.0,
                reason="Camera frames are stale or camera disconnected — analysis suspended",
                is_provisional=self._config.provisional,
            )

        if not tracks:
            return MovementAnalysisResult(
                is_valid=True,
                tracked_count=0,
                inactive_count=0,
                avg_movement_score=0.0,
                reason="No silkworms currently tracked",
                is_provisional=self._config.provisional,
            )

        total_movement = 0.0
        inactive_ids: list[int] = []
        now_s = time.monotonic()

        for track in tracks:
            history = track.centroid_history
            if len(history) < 2:
                continue

            # Calculate total displacement across observations
            p_start = history[0]
            p_end = history[-1]
            dt = p_end[2] - p_start[2]
            dist = math.sqrt((p_end[0] - p_start[0]) ** 2 + (p_end[1] - p_start[1]) ** 2)

            speed = (dist / dt) if dt > 0.001 else 0.0
            total_movement += speed

            # Check inactivity condition
            has_enough_obs = len(history) >= self._config.min_observations_for_inactivity
            has_enough_time = dt >= self._config.inactivity_duration_s

            if has_enough_obs and has_enough_time and speed < self._config.movement_score_threshold:
                inactive_ids.append(track.track_id)

        avg_score = (total_movement / len(tracks)) if tracks else 0.0

        return MovementAnalysisResult(
            is_valid=True,
            tracked_count=len(tracks),
            inactive_count=len(inactive_ids),
            avg_movement_score=round(avg_score, 4),
            inactive_track_ids=inactive_ids,
            reason="Analysis valid",
            is_provisional=self._config.provisional,
        )
