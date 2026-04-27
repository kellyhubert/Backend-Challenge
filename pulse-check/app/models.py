# models.py — Defines the shape of data coming IN and going OUT of the API.
# Pydantic validates every request automatically — bad input is rejected before
# it ever reaches our business logic.

from pydantic import BaseModel, Field, EmailStr
from typing import Optional


# ─── Request Models ───────────────────────────────────────────────────────────

class CreateMonitorRequest(BaseModel):
    """Body sent by the device administrator when registering a new monitor."""

    id: str = Field(..., description="Unique device identifier, e.g. 'device-123'")
    timeout: int = Field(..., gt=0, description="Countdown duration in seconds")
    alert_email: str = Field(..., description="Email to notify when the device goes down")


# ─── Response Models ──────────────────────────────────────────────────────────

class MonitorResponse(BaseModel):
    """
    Returned after creating a monitor or checking its status.
    `time_remaining` is None when the monitor is paused or already down.
    """

    id: str
    status: str                          # "active" | "paused" | "down"
    timeout: int                         # original timeout in seconds
    alert_email: str
    time_remaining: Optional[float]      # seconds left on the countdown (None if not running)
    last_heartbeat: Optional[str]        # ISO timestamp of last heartbeat, or None
    created_at: str                      # ISO timestamp of when the monitor was registered


class MessageResponse(BaseModel):
    """Simple confirmation message returned for heartbeat and pause actions."""

    message: str
