"""Software watchdog for task liveness and deadlock detection.

Monitors heartbeats from sensor, vision, decision, and cloud sync loops.
Ensures that a stuck camera, blocked network socket, or deadlocked thread
is promptly detected without hanging the safety mechanisms.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class WatchdogTask:
    name: str
    timeout_s: float
    last_heartbeat_mono: float
    is_active: bool = True


class SoftwareWatchdog:
    """Tracks task heartbeats and alerts on stalled loops."""

    def __init__(self, check_interval_s: float = 2.0):
        self._check_interval = check_interval_s
        self._tasks: dict[str, WatchdogTask] = {}
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._stall_handlers: list[Callable[[str, float], None]] = []

    def register_task(self, name: str, timeout_s: float) -> None:
        """Register a subsystem task with a maximum permitted stall duration."""
        with self._lock:
            self._tasks[name] = WatchdogTask(
                name=name,
                timeout_s=timeout_s,
                last_heartbeat_mono=time.monotonic(),
            )

    def heartbeat(self, name: str) -> None:
        """Record a heartbeat signal from a running subsystem loop."""
        with self._lock:
            task = self._tasks.get(name)
            if task:
                task.last_heartbeat_mono = time.monotonic()

    def add_stall_handler(self, handler: Callable[[str, float], None]) -> None:
        """Add callback for stalled task (receives task_name, stall_duration_s)."""
        self._stall_handlers.append(handler)

    def start(self) -> None:
        """Start background watchdog monitoring thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(
                target=self._monitor_loop,
                name="SoftwareWatchdog",
                daemon=True,
            )
            self._thread.start()

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self._check_interval)
            now = time.monotonic()
            stalled: list[tuple[str, float]] = []

            with self._lock:
                for name, task in self._tasks.items():
                    if not task.is_active:
                        continue
                    dt = now - task.last_heartbeat_mono
                    if dt > task.timeout_s:
                        stalled.append((name, dt))

            for name, dt in stalled:
                logger.critical(
                    f"WATCHDOG: Task '{name}' has stalled! No heartbeat for {dt:.1f}s (timeout={self._tasks[name].timeout_s}s)"
                )
                for handler in self._stall_handlers:
                    try:
                        handler(name, dt)
                    except Exception as err:
                        logger.error(f"Error in watchdog stall handler: {err}")

    def stop(self) -> None:
        """Stop watchdog thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
