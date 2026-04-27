# Idempotency Gateway — The "Pay-Once" Protocol

A REST API middleware that guarantees every payment is processed **exactly once**, even when clients retry due to network timeouts.

---

## Architecture

```
Client
  │
  │  POST /process-payment
  │  Idempotency-Key: <uuid>
  │
  ▼
┌─────────────────────────────────────────────────────┐
│                  FastAPI App                        │
│                                                     │
│  1. Hash the request payload (SHA-256)              │
│  2. Check IdempotencyStore                          │
│     ├── Key not found   → acquire lock → process   │
│     ├── Key found + same payload → return cache    │
│     └── Key found + diff payload → reject 422      │
│                                                     │
│  ┌──────────────────────────────────────────────┐  │
│  │           IdempotencyStore (in-memory)        │  │
│  │  key → { payload_hash, response, created_at } │  │
│  │  Per-key asyncio.Lock (race condition guard)  │  │
│  └──────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Sequence for a race condition (two simultaneous requests):**

```
Client A ──► acquire lock ──► process (2s) ──► save ──► return 201
Client B ──► wait at lock ──────────────────► read cache ──► return 201 (X-Cache-Hit: true)
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

### POST `/process-payment`

Process a payment request.

**Headers**

| Header            | Required | Description                              |
|-------------------|----------|------------------------------------------|
| `Idempotency-Key` | Yes      | Client-generated unique key (e.g. UUID) |
| `Content-Type`    | Yes      | `application/json`                       |

**Request Body**

```json
{
  "amount": 100.0,
  "currency": "RwF",
  "customer_id": "cust_001",
  "description": "Order #42"
}
```

**Responses**

| Status | When | Notes |
|--------|------|-------|
| `201`  | New payment processed | Includes `transaction_id` |
| `201` + `X-Cache-Hit: true` | Duplicate request (same key + same payload) | Cached response replayed instantly |
| `422`  | Same key, different payload | Conflict — key already used for a different payment |
| `422`  | Missing/invalid request fields | FastAPI automatic validation |

**Success response (new or cached)**

```json
{
  "status": "success",
  "message": "Charged 100.0 RwF",
  "transaction_id": "1d0261c2-2c7c-49f3-8876-5e7359ab9e8d",
  "amount": 100,
  "currency": "RwF"
}
```

---

### GET `/health`

Returns `200 {"status": "ok"}` — useful for load balancers / uptime checks.

---

## Design Decisions

### 1. Double-Checked Locking (Race Condition Handling)
The store is checked **twice** — once before acquiring the lock (fast path, no blocking) and once after (to catch the case where a concurrent request finished while we were waiting). This ensures we never process the same key twice, even under heavy concurrency.

### 2. Per-Key Locks
Each idempotency key gets its own `asyncio.Lock`. This means two requests with **different** keys never block each other — only requests that share the same key are serialised.

### 3. Payload Hashing (SHA-256)
Instead of storing the raw payload, we store a SHA-256 fingerprint. This lets us compare payloads cheaply without keeping duplicate data in memory.

---

## Developer's Choice Feature — Idempotency Key TTL (24-hour expiry)

**What it does:** Every stored key automatically expires after 24 hours. An expired key is treated as new — the next request with that key will be processed as a fresh payment.

**Why this matters for fintech:**
- Stripe, PayPal, and most payment processors implement the same 24-hour window.
- Prevents the in-memory store from growing forever in long-running deployments.
- Aligns with compliance requirements — you shouldn't be able to replay a payment from days ago by reusing an old key.

**Where it lives:** `app/store.py` → `IdempotencyEntry.is_expired()` and `TTL_SECONDS = 86_400`.

---

## Project Structure

```
idempotency-gateway/
├── app/
│   ├── __init__.py      # Marks app/ as a Python package
│   ├── main.py          # FastAPI app, routes, and core request logic
│   ├── models.py        # Pydantic request/response models
│   └── store.py         # In-memory idempotency store with TTL and locks
├── requirements.txt
├── .gitignore
└── README.md
```
