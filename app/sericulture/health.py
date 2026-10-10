"""System health monitor for Raspberry Pi 5.

Collects CPU usage, RAM usage, disk usage, CPU temperature, uptime, and
aggregates component statuses into periodic health snapshots.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SystemHealthMetrics:
    """Snapshot of Raspberry Pi hardware and OS metrics."""
    cpu_usage_pct: float
    ram_usage_pct: float
    disk_used_pct: float
    disk_free_mb: float
    cpu_temp_c: float
    uptime_s: float
    service_status: str


class HealthMonitor:
    """Monitors Pi system hardware resources and thermal conditions."""

    def __init__(self, db_dir: str = "/var/lib/sericulture"):
        self._db_dir = db_dir
        self._start_monotonic = time.monotonic()
        self._prev_idle: int = 0
        self._prev_total: int = 0

    def read_cpu_temperature_c(self) -> float:
        """Read Raspberry Pi SoC temperature in °C."""
        thermal_path = "/sys/class/thermal/thermal_zone0/temp"
        try:
            if os.path.exists(thermal_path):
                with open(thermal_path, "r") as f:
                    content = f.read().strip()
                    return round(float(content) / 1000.0, 1)
        except Exception as e:
            logger.debug(f"Unable to read thermal zone: {e}")
        return 0.0

    def read_cpu_usage_pct(self) -> float:
        """Read overall CPU usage percentage from /proc/stat."""
        try:
            if os.path.exists("/proc/stat"):
                with open("/proc/stat", "r") as f:
                    first_line = f.readline()
                parts = first_line.split()[1:8]
                values = [int(p) for p in parts]
                idle = values[3] + values[4]  # idle + iowait
                total = sum(values)

                if self._prev_total > 0:
                    diff_idle = idle - self._prev_idle
                    diff_total = total - self._prev_total
                    usage = 100.0 * (1.0 - (diff_idle / diff_total)) if diff_total > 0 else 0.0
                    self._prev_idle = idle
                    self._prev_total = total
                    return round(max(0.0, min(100.0, usage)), 1)

                self._prev_idle = idle
                self._prev_total = total
                return 0.0
        except Exception as e:
            logger.debug(f"Unable to read CPU stat: {e}")
        return 0.0

    def read_ram_usage_pct(self) -> float:
        """Read RAM usage percentage from /proc/meminfo."""
        try:
            if os.path.exists("/proc/meminfo"):
                mem = {}
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            key = parts[0].strip()
                            val_str = parts[1].strip().split()[0]
                            mem[key] = int(val_str)
                total = mem.get("MemTotal", 0)
                available = mem.get("MemAvailable", 0)
                if total > 0:
                    used = total - available
                    return round((used / total) * 100.0, 1)
        except Exception as e:
            logger.debug(f"Unable to read /proc/meminfo: {e}")
        return 0.0

    def read_disk_metrics(self) -> tuple[float, float]:
        """Returns (disk_used_pct, disk_free_mb)."""
        target = self._db_dir if os.path.exists(self._db_dir) else "/"
        try:
            usage = shutil.disk_usage(target)
            used_pct = round((usage.used / usage.total) * 100.0, 1)
            free_mb = round(usage.free / (1024 * 1024), 1)
            return used_pct, free_mb
        except Exception as e:
            logger.debug(f"Unable to read disk usage: {e}")
            return 0.0, 0.0

    def read_uptime_s(self) -> float:
        """Read system uptime in seconds from /proc/uptime, or fallback to process monotonic."""
        try:
            if os.path.exists("/proc/uptime"):
                with open("/proc/uptime", "r") as f:
                    return round(float(f.readline().split()[0]), 1)
        except Exception:
            pass
        return round(time.monotonic() - self._start_monotonic, 1)

    def get_metrics(self, service_status: str = "RUNNING") -> SystemHealthMetrics:
        """Collect and return full system health metrics."""
        disk_used_pct, disk_free_mb = self.read_disk_metrics()
        return SystemHealthMetrics(
            cpu_usage_pct=self.read_cpu_usage_pct(),
            ram_usage_pct=self.read_ram_usage_pct(),
            disk_used_pct=disk_used_pct,
            disk_free_mb=disk_free_mb,
            cpu_temp_c=self.read_cpu_temperature_c(),
            uptime_s=self.read_uptime_s(),
            service_status=service_status,
        )
