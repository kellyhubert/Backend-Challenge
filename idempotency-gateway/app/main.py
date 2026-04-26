# Contains:
#   - App setup
#   - POST /process-payment  (the core idempotency endpoint)
#   - GET  /health           (quick liveness check)

import asyncio
import logging
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from app.models import PaymentRequest
from app.store import IdempotencyStore


# Simple console logger so we can trace what's happening per request.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)



app = FastAPI(
    title="Idempotency Gateway",
    description="Pay-Once Protocol — ensures each payment is processed exactly once.",
    version="1.0.0",
)

# One store instance shared across all requests (lives in-process memory).
store = IdempotencyStore()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/process-payment", status_code=201)
async def process_payment(
    payment: PaymentRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    """
    Process a payment exactly once.

    Flow:
      1. Hash the incoming payload.
      2. Check the store (fast path — no lock needed yet).
         a. Key found + same payload  → return cached response with X-Cache-Hit: true
         b. Key found + diff payload  → reject 422
         c. Key not found             → continue to step 3
      3. Acquire the per-key lock (handles race conditions).
      4. Re-check the store (another coroutine may have processed it while we waited).
      5. Process the payment (simulate 2-second delay).
      6. Save the response and return it.
    """

    # Step 1: fingerprint the request body to detect payload changes for the same key.
    # We'll compare this fingerprint against what was stored for the same key.
    payload_hash = store.hash_payload(payment.model_dump())

    #  Step 2: fast-path check (no lock) — if the key exists, verify the payload and return the cached response.
    existing = store.get(idempotency_key)

    if existing:
        # Same key seen before — verify the payload hasn't changed.
        if existing.payload_hash != payload_hash:
            # Different payload with the same key — this is a conflict / potential fraud.
            logger.warning("Payload mismatch for key: %s", idempotency_key)
            raise HTTPException(
                status_code=422,
                detail=(
                    "Idempotency-Key already used with a different payload. "
                    "Use a new key for a different payment."
                ),
            )

        # Payload matches — replay the original response immediately (no processing).
        logger.info("Cache hit (fast path) for key: %s", idempotency_key)
        return _cached_response(existing.response_body, existing.status_code)

    # ── Step 3: acquire the per-key lock ──────────────────────────────────────
    # If two identical requests arrive at the same millisecond, one will acquire
    # the lock first and process the payment.  The other will wait here.
    key_lock = await store.get_lock(idempotency_key)

    async with key_lock:

        # ── Step 4: re-check inside the lock (double-checked locking) ─────────
        # The request that was waiting may now find a completed entry — return it.
        existing = store.get(idempotency_key)

        if existing:
            if existing.payload_hash != payload_hash:
                logger.warning("Payload mismatch (post-lock) for key: %s", idempotency_key)
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Idempotency-Key already used with a different payload. "
                        "Use a new key for a different payment."
                    ),
                )

            logger.info("Cache hit (post-lock) for key: %s", idempotency_key)
            return _cached_response(existing.response_body, existing.status_code)

        # ── Step 5: process the payment ───────────────────────────────────────
        logger.info("Processing payment for key: %s | amount: %s %s",
                    idempotency_key, payment.amount, payment.currency)

        # Simulate the time a real payment processor (Stripe, PayStack, etc.) would take.
        await asyncio.sleep(2)

        # Build the response body — generate a unique transaction ID for this payment.
        transaction_id = str(uuid.uuid4())
        response_body = {
            "status": "success",
            "message": f"Charged {payment.amount} {payment.currency}",
            "transaction_id": transaction_id,
            "amount": payment.amount,
            "currency": payment.currency,
        }

        # ── Step 6: persist and return ────────────────────────────────────────
        store.save(idempotency_key, payload_hash, response_body, status_code=201)
        logger.info("Payment complete. Transaction ID: %s", transaction_id)

        return JSONResponse(content=response_body, status_code=201)


@app.get("/health")
async def health():
    """Simple liveness probe — returns 200 if the server is running."""
    return {"status": "ok"}


# Helper  

def _cached_response(response_body: dict, status_code: int) -> JSONResponse:
    """
    Build a JSONResponse for a replayed (cached) payment.
    The X-Cache-Hit header tells the client this is a replay, not a new charge.
    """
    response = JSONResponse(content=response_body, status_code=status_code)
    response.headers["X-Cache-Hit"] = "true"
    return response
