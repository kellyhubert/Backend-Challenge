# Endpoints:
#   POST /monitors              — register a device monitor
#   POST /monitors/{id}/heartbeat — reset the countdown timer
#   POST /monitors/{id}/pause   — pause the timer (maintenance window)
#   GET  /monitors/{id}         — check the current status of a monitor (Developer's Choice)
#   GET  /health                — liveness check

import logging
import time

from fastapi import FastAPI, HTTPException

from app.models import CreateMonitorRequest, MonitorResponse, MessageResponse
from app.monitor import MonitorManager, DOWN, PAUSED


# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


# ─── App + shared manager ─────────────────────────────────────────────────────

app = FastAPI(
    title="Pulse-Check API",
    description="Dead Man's Switch — alerts when a device stops sending heartbeats.",
    version="1.0.0",
)

# One MonitorManager shared across all requests.
# It holds all registered monitors and their background timer tasks.
manager = MonitorManager()


# ─── Helper ───────────────────────────────────────────────────────────────────

def _to_response(monitor) -> MonitorResponse:
    """Convert a Monitor dataclass into the API response shape."""
    return MonitorResponse(
        id=monitor.id,
        status=monitor.status,
        timeout=monitor.timeout,
        alert_email=monitor.alert_email,
        time_remaining=monitor.time_remaining(),
        last_heartbeat=(
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(monitor.last_heartbeat))
            if monitor.last_heartbeat else None
        ),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(monitor.created_at)),
    )


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/monitors", status_code=201, response_model=MonitorResponse)
async def create_monitor(body: CreateMonitorRequest):
    """
    Register a new device monitor and start its countdown timer.

    The system will fire an alert (log a JSON message) if no heartbeat
    is received within `timeout` seconds.
    """
    monitor = await manager.create(
        monitor_id=body.id,
        timeout=body.timeout,
        alert_email=body.alert_email,
    )
    logger.info("Created monitor for device: %s", body.id)
    return _to_response(monitor)


@app.post("/monitors/{monitor_id}/heartbeat", response_model=MessageResponse)
async def heartbeat(monitor_id: str):
    """
    Reset the countdown timer for a device — it's confirming it's still alive.

    - Timer resets to its full duration.
    - If the monitor was paused, it becomes active again.
    - Returns 404 if the monitor_id is not registered.
    - Returns 409 if the device is already marked as down (must re-register).
    """
    monitor = await manager.heartbeat(monitor_id)

    # Monitor not found — device was never registered
    if monitor is None:
        raise HTTPException(status_code=404, detail=f"Monitor '{monitor_id}' not found.")

    # Device already went down — heartbeat can't revive it, must re-register
    if monitor.status == DOWN:
        raise HTTPException(
            status_code=409,
            detail=f"Monitor '{monitor_id}' is already down. Re-register to start a new session.",
        )

    return MessageResponse(message=f"Heartbeat received. Timer reset to {monitor.timeout}s.")


@app.post("/monitors/{monitor_id}/pause", response_model=MessageResponse)
async def pause_monitor(monitor_id: str):
    """
    Pause the countdown timer — stops the clock without firing an alert.
    Useful during planned maintenance windows.

    Calling heartbeat after this will automatically un-pause and restart the timer.
    Returns 404 if the monitor_id is not registered.
    """
    monitor = await manager.pause(monitor_id)

    if monitor is None:
        raise HTTPException(status_code=404, detail=f"Monitor '{monitor_id}' not found.")

    # Already paused — return a helpful message (idempotent)
    if monitor.status == PAUSED:
        return MessageResponse(message=f"Monitor '{monitor_id}' is already paused.")

    # Was already down — nothing to pause
    if monitor.status == DOWN:
        raise HTTPException(
            status_code=409,
            detail=f"Monitor '{monitor_id}' is already down and cannot be paused.",
        )

    return MessageResponse(message=f"Monitor '{monitor_id}' paused. Send a heartbeat to resume.")


# ─── Developer's Choice: Status Endpoint ──────────────────────────────────────

@app.get("/monitors/{monitor_id}", response_model=MonitorResponse)
async def get_monitor(monitor_id: str):
    """
    [Developer's Choice] Retrieve the full current status of a monitor.

    Returns the device state, time remaining on the countdown, last heartbeat
    timestamp, and alert email — everything a monitoring dashboard would need.

    Why this is useful:
      Without this endpoint, there's no way to check what's happening to a device
      without waiting for an alert to fire. Operations teams need a way to query
      device health on demand — this is that endpoint.
    """
    monitor = manager.get(monitor_id)

    if monitor is None:
        raise HTTPException(status_code=404, detail=f"Monitor '{monitor_id}' not found.")

    return _to_response(monitor)


# Health Check  

@app.get("/health")
async def health():
    """Liveness probe — confirms the server is running."""
    return {"status": "ok"}
