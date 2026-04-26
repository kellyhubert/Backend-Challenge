# models.py — Defines the shape of data coming IN and going OUT of the API.
# We use Pydantic so FastAPI can automatically validate requests and reject bad input.

from pydantic import BaseModel, Field
from typing import Optional


# ─── Request Model ────────────────────────────────────────────────────────────

class PaymentRequest(BaseModel):
    """
    The body the client sends when making a payment request.
    FastAPI will automatically return 422 if any required field is missing or wrong type.
    """

    amount: float = Field(..., gt=0, description="Amount to charge (must be greater than 0)")
    currency: str = Field(..., min_length=3, max_length=3, description="3-letter currency code, e.g. GHS, USD")
    customer_id: str = Field(..., description="Unique identifier for the customer being charged")
    description: Optional[str] = Field(None, description="Optional note about the payment")


# ─── Response Model ───────────────────────────────────────────────────────────

class PaymentResponse(BaseModel):
    """
    The body we send back after a successful payment (new or cached).
    """

    status: str                  # "success"
    message: str                 # e.g. "Charged 100.0 GHS"
    transaction_id: str          # Unique ID generated for this payment
    amount: float                # Echoed back from the request
    currency: str                # Echoed back from the request


# ─── Error Response Model ─────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """
    Consistent error shape returned for 4xx responses.
    """

    detail: str                  # Human-readable description of what went wrong
