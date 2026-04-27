# monitor.py — Core business logic for the Dead Man's Switch system.
#
# Responsibilities:
#   1. Store all registered monitors in memory.
#   2. Start a countdown timer (asyncio background task) for each monitor.
#   3. Fire a JSON alert and mark the monitor as "down" if no heartbeat arrives in time.
#   4. Reset the timer on heartbeat.
#   5. Pause / un-pause the timer on demand.

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ─── Monitor States ───────────────────────────────────────────────────────────

# A monitor can be in one of three states at any point:
#   active  — timer is running, waiting for the next heartbeat
#   paused  — timer is stopped (maintenance window), no alert will fire
#   down    — timer expired, alert was fired, device is considered offline

ACTIVE = "active"
PAUSED = "paused"
DOWN   = "down"


# ─── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class Monitor:
    """Everything we track about a single registered device."""

    id: str
    timeout: int                          # countdown duration in seconds
    alert_email: str
    status: str = ACTIVE
    created_at: float = field(default_factory=time.time)
    last_heartbeat: Optional[float] = None
    deadline: Optional[float] = None      # Unix timestamp when the timer will fire
    _task: Optional[asyncio.Task] = field(default=None, repr=False)

    def time_remaining(self) -> Optional[float]:
        """
        Returns seconds left before the alert fires.
        Returns None if the timer is not currently running (paused or down).
        """
        if self.status == ACTIVE and self.deadline is not None:
            remaining = self.deadline - time.time()
            return max(0.0, remaining)  # never return a negative number
        return None


# ─── Monitor Manager ──────────────────────────────────────────────────────────

class MonitorManager:
    """
    Manages all monitors and their background countdown tasks.

    Every time a timer needs to start (on create or heartbeat), we:
      1. Cancel any existing task for that monitor.
      2. Create a new asyncio.Task that sleeps for `timeout` seconds.
      3. When the task wakes up, it fires the alert and marks the monitor as down.
    """

    def __init__(self):
        # Maps device id → Monitor object
        self._monitors: Dict[str, Monitor] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, monitor_id: str) -> Optional[Monitor]:
        """Return the Monitor for `monitor_id`, or None if not registered."""
        return self._monitors.get(monitor_id)

    async def create(self, monitor_id: str, timeout: int, alert_email: str) -> Monitor:
        """
        Register a new monitor and immediately start its countdown.
        If the same id is re-registered, the old timer is cancelled first.
        """
        # Cancel any existing timer for this id before overwriting
        if monitor_id in self._monitors:
            await self._cancel_timer(monitor_id)

        monitor = Monitor(id=monitor_id, timeout=timeout, alert_email=alert_email)
        self._monitors[monitor_id] = monitor

        # Start the countdown immediately
        await self._start_timer(monitor)

        logger.info("Monitor registered: %s (timeout: %ds)", monitor_id, timeout)
        return monitor

    async def heartbeat(self, monitor_id: str) -> Optional[Monitor]:
        """
        Reset the countdown for a monitor — the device is confirming it's alive.

        Returns:
          - The updated Monitor on success.
          - None if the monitor_id is not registered (caller returns 404).
        """
        monitor = self.get(monitor_id)
        if monitor is None:
            return None

        # A "down" monitor cannot be revived by a heartbeat — it must be re-registered.
        if monitor.status == DOWN:
            return monitor

        # Cancel the current countdown (whether active or paused)
        await self._cancel_timer(monitor_id)

        # Mark as active again (handles the paused → active transition)
        monitor.status = ACTIVE
        monitor.last_heartbeat = time.time()

        # Restart the full countdown from scratch
        await self._start_timer(monitor)

        logger.info("Heartbeat received for: %s — timer reset to %ds", monitor_id, monitor.timeout)
        return monitor

    async def pause(self, monitor_id: str) -> Optional[Monitor]:
        """
        Pause the countdown — no alert will fire while paused.
        Calling heartbeat later will un-pause and restart the timer.

        Returns:
          - The updated Monitor on success.
          - None if the monitor_id is not registered (caller returns 404).
        """
        monitor = self.get(monitor_id)
        if monitor is None:
            return None

        # Pausing an already-paused or down monitor is a no-op
        if monitor.status in (PAUSED, DOWN):
            return monitor

        # Stop the running timer
        await self._cancel_timer(monitor_id)
        monitor.status = PAUSED

        logger.info("Monitor paused: %s", monitor_id)
        return monitor

    # ── Internal Timer Helpers ────────────────────────────────────────────────

    async def _start_timer(self, monitor: Monitor) -> None:
        """
        Create a background asyncio task that will fire the alert after `timeout` seconds.
        Also records the deadline so we can compute `time_remaining`.
        """
        monitor.deadline = time.time() + monitor.timeout
        monitor._task = asyncio.create_task(
            self._countdown(monitor.id, monitor.timeout)
        )

    async def _cancel_timer(self, monitor_id: str) -> None:
        """
        Cancel the running background task for `monitor_id`, if any.
        Waits for the cancellation to complete so there's no race condition.
        """
        monitor = self._monitors.get(monitor_id)
        if monitor and monitor._task and not monitor._task.done():
            monitor._task.cancel()
            try:
                await monitor._task  # wait for the CancelledError to propagate
            except asyncio.CancelledError:
                pass  # expected — the task was cancelled intentionally
        if monitor:
            monitor._task = None
            monitor.deadline = None

    async def _countdown(self, monitor_id: str, timeout: int) -> None:
        """
        The background task body.
        Sleeps for `timeout` seconds, then fires the alert if not cancelled.
        This runs silently in the background — it only wakes up when the timer expires.
        """
        await asyncio.sleep(timeout)

        monitor = self._monitors.get(monitor_id)
        if monitor is None:
            return  # monitor was removed before the timer fired

        # Update state to "down" — device is considered offline
        monitor.status = DOWN
        monitor.deadline = None

        # Fire the alert — log a structured JSON message (simulates sending an email)
        alert = {
            "ALERT": f"Device {monitor_id} is down!",
            "alert_email": monitor.alert_email,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        logger.critical("🚨 %s", json.dumps(alert))
