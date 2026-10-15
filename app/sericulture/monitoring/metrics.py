"""Runtime performance metrics and counters.

Maintains thread-safe counters, latency trackers, and sync throughput metrics.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


class MetricsTracker:
    """Thread-safe collector for runtime metrics and counters."""

    def __init__(self):
        self._lock = Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._latencies: dict[str, list[float]] = {}

    def inc(self, metric: str, amount: int = 1) -> None:
        """Increment a named counter."""
        with self._lock:
            self._counters[metric] = self._counters.get(metric, 0) + amount

    def set_gauge(self, metric: str, value: float) -> None:
        """Set a named gauge value."""
        with self._lock:
            self._gauges[metric] = value

    def record_latency(self, metric: str, duration_ms: float) -> None:
        """Record an operation latency sample (keeps recent 100 samples)."""
        with self._lock:
            samples = self._latencies.setdefault(metric, [])
            samples.append(duration_ms)
            if len(samples) > 100:
                samples.pop(0)

    def get_summary(self) -> dict[str, Any]:
        """Return dict of current metrics."""
        with self._lock:
            res: dict[str, Any] = {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "latencies_avg_ms": {},
            }
            for k, samples in self._latencies.items():
                if samples:
                    res["latencies_avg_ms"][k] = round(sum(samples) / len(samples), 2)
            return res
