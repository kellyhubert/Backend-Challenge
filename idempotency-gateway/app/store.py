# store.py — The in-memory idempotency store.
#
# Responsibilities:
#   1. Save a processed payment response, keyed by the client's Idempotency-Key.
#   2. Look up a previous response when the same key is seen again.
#   3. Expire keys after 24 hours (Developer's Choice safety feature).
#   4. Provide a per-key async lock so concurrent requests with the same key
#      are serialised instead of processed twice (race condition handling).

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Dict, Optional


# ─── Configuration ────────────────────────────────────────────────────────────

# Keys expire after 24 hours — industry standard (Stripe, PayPal both use this).
# After expiry the key is treated as brand-new, allowing a fresh payment attempt.
TTL_SECONDS = 86_400  # 60 * 60 * 24


# ─── Data stored per idempotency key ─────────────────────────────────────────

@dataclass
class IdempotencyEntry:
    """Everything we remember about a completed payment."""

    payload_hash: str    # SHA-256 of the original request body (used to detect payload mismatches)
    response_body: dict  # The exact JSON we returned to the client
    status_code: int     # HTTP status code we returned (201)
    created_at: float    # Unix timestamp — used to check TTL

    def is_expired(self) -> bool:
        """Returns True if this entry is older than TTL_SECONDS."""
        return (time.time() - self.created_at) > TTL_SECONDS


# ─── The Store ────────────────────────────────────────────────────────────────

class IdempotencyStore:
    """
    Thread-safe, in-memory store for idempotency keys.

    Two internal dictionaries:
      _store      — maps idempotency key → IdempotencyEntry
      _key_locks  — maps idempotency key → asyncio.Lock (one lock per key)

    The per-key lock is what prevents the race condition:
    if two requests arrive at the same time with the same key,
    the second one will wait at the lock until the first finishes.
    """

    def __init__(self):
        self._store: Dict[str, IdempotencyEntry] = {}

        # Each key gets its own lock so keys don't block each other.
        self._key_locks: Dict[str, asyncio.Lock] = {}

        # A single "meta" lock protects the _key_locks dictionary itself
        # from being modified by two coroutines simultaneously.
        self._meta_lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_lock(self, key: str) -> asyncio.Lock:
        """
        Return the asyncio.Lock for `key`, creating one if it doesn't exist yet.
        Uses _meta_lock so two coroutines can't both try to create the same lock.
        """
        async with self._meta_lock:
            if key not in self._key_locks:
                self._key_locks[key] = asyncio.Lock()
            return self._key_locks[key]

    def get(self, key: str) -> Optional[IdempotencyEntry]:
        """
        Return the stored entry for `key`, or None if it doesn't exist / has expired.
        Expired entries are cleaned up on read (lazy cleanup — no background thread needed).
        """
        entry = self._store.get(key)

        if entry is None:
            return None  # Key was never seen before

        if entry.is_expired():
            # Key existed but is too old — treat it as a new request
            self._store.pop(key, None)
            self._key_locks.pop(key, None)
            return None

        return entry

    def save(self, key: str, payload_hash: str, response_body: dict, status_code: int) -> None:
        """Persist a completed payment response so we can replay it for duplicate requests."""
        self._store[key] = IdempotencyEntry(
            payload_hash=payload_hash,
            response_body=response_body,
            status_code=status_code,
            created_at=time.time(),
        )

    # ── Utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def hash_payload(payload: dict) -> str:
        """
        Produce a stable SHA-256 fingerprint of a request payload.
        `sort_keys=True` ensures {"a":1,"b":2} and {"b":2,"a":1} produce the same hash.
        This fingerprint is what we compare to detect payload mismatches (User Story 3).
        """
        serialised = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(serialised.encode()).hexdigest()
