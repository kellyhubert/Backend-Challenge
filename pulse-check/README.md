# Pulse-Check API — The "Watchdog" Sentinel

A Dead Man's Switch API that monitors remote devices by tracking heartbeat signals. If a device stops sending heartbeats before its timer expires, the system automatically fires an alert.

---

## Architecture

```
Device / Client
      │
      │  POST /monitors              → register + start timer
      │  POST /monitors/{id}/heartbeat → reset timer
      │  POST /monitors/{id}/pause   → stop timer (maintenance)
      │  GET  /monitors/{id}         → check status
      │
      ▼
┌──────────────────────────────────────────────────────────┐
│                     FastAPI App                          │
│                                                          │
│   ┌──────────────────────────────────────────────────┐   │
│   │              MonitorManager                      │   │
│   │                                                  │   │
│   │  monitors: { device-id → Monitor }               │   │
│   │                                                  │   │
│   │  Each Monitor holds:                             │   │
│   │    • status  (active | paused | down)            │   │
│   │    • timeout (seconds)                           │   │
│   │    • deadline (when the timer fires)             │   │
│   │    • asyncio.Task (the background countdown)     │   │
│   └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

**State Machine:**

```
  register
     │
     ▼
 [ active ] ◄──── heartbeat ────┐
     │                          │
     │ timeout expires          │ heartbeat (un-pauses)
     ▼                          │
  [ down ]        [ paused ] ───┘
                      ▲
                      │ pause
                  [ active ]
```

**Sequence — device goes offline:**

```
Device ──► POST /monitors  ──► timer starts (60s)
          ... 60 seconds pass, no heartbeat ...
Timer  ──► fires alert: {"ALERT": "Device X is down!", "time": "..."}
           monitor.status = "down"
```

**Sequence — normal heartbeat cycle:**

```
Device ──► POST /monitors/{id}/heartbeat ──► timer resets to 60s
Device ──► POST /monitors/{id}/heartbeat ──► timer resets to 60s
           (repeats every cycle — device stays "active")
```

---

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the server
uvicorn app.main:app --reload
```

Server runs at `http://localhost:8000`.
Interactive docs at `http://localhost:8000/docs`.

---

## API Reference

### POST `/monitors`
Register a new device and start its countdown timer.

**Request body**
```json
{
  "id": "device-123",
  "timeout": 300,
  "alert_email": "admin@critmon.com"
}
```

**Response `201`**
```json
{
  
  "id": "device-123",
  "status": "active",
  "timeout": 300,
  "alert_email": "admin@critmon.com",
  "time_remaining": 299.9980070590973,
  "last_heartbeat": null,
  "created_at": "2026-04-27T09:52:40Z"

}
```

---

### POST `/monitors/{id}/heartbeat`
Reset the countdown — device confirms it is alive.

| Scenario | Status | Response |
|----------|--------|----------|
| Monitor exists and is active/paused | `200` | Timer reset confirmation |
| Monitor not found | `404` | Error message |
| Monitor already down | `409` | Must re-register |

**Response `200`**
```json
{ "message": "Heartbeat received. Timer reset to 300s." }
```

> Sending a heartbeat to a **paused** monitor automatically un-pauses it and restarts the full countdown.

---

### POST `/monitors/{id}/pause`
Stop the countdown without firing an alert. Use during planned maintenance.

**Response `200`**
```json
{ "message": "Monitor 'device-123' is already paused." }
```

---

### GET `/monitors/{id}`
*(Developer's Choice)* Retrieve the current state of a monitor.

**Response `200`**
```json
{
  "id": "device-123",
  "status": "paused",
  "timeout": 300,
  "alert_email": "admin@critmon.com",
  "time_remaining": null,
  "last_heartbeat": "2026-04-27T09:53:24Z",
  "created_at": "2026-04-27T09:52:40Z"
}
```

---

### GET `/health`
Liveness probe. Returns `200 {"status": "ok"}`.

---

## Design Decisions

### 1. asyncio Background Tasks for Timers
Each monitor gets its own `asyncio.Task` that sleeps for `timeout` seconds. If a heartbeat arrives, the task is cancelled and a new one is started from scratch. This approach is lightweight — no threads, no schedulers, no external queues needed.

### 2. Three-State Machine (active → paused → down)
Devices move through clear states. Once `down`, a monitor cannot be revived by a heartbeat — it must be re-registered. This prevents accidentally resetting a device that genuinely went offline.

### 3. Structured Alert Logging
The alert fires as a structured JSON log line at `CRITICAL` level. In a production system this would be replaced with an actual email/webhook call — the log line is designed to be easy to swap out.

---

## Developer's Choice Feature — `GET /monitors/{id}` Status Endpoint

**What it does:** Returns the complete current state of any registered monitor — including `time_remaining`, `last_heartbeat`, and `status`.

**Why it was added:**
The core spec only defines write endpoints (register, heartbeat, pause). Without a read endpoint, there is no way to inspect what is happening to a device without waiting for an alert to fire. Any real monitoring dashboard needs to be able to query device health on demand — for example, to show a technician how long until the next expected heartbeat. This endpoint fills that gap.

---

## Project Structure

```
pulse-check/
├── app/
│   ├── __init__.py     # Marks app/ as a Python package
│   ├── main.py         # FastAPI app and all route handlers
│   ├── models.py       # Pydantic request/response models
│   └── monitor.py      # MonitorManager — timer logic, state, alert firing
├── requirements.txt
├── .gitignore
└── README.md
```
