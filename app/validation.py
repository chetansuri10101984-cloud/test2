"""
claims-api — request validation.

Validates incoming POST /claims payloads. A validation failure is surfaced to
the caller as HTTP 422 Unprocessable Entity.
"""

from __future__ import annotations

# Upstream systems generate reference strings at a fixed width. 64 is a valid,
# very common length in real traffic.
MAX_REFERENCE_LEN = 64

# The claim types the service accepts. The query layer (query.sql) is the
# source of truth for what the database will match.
ALLOWED_CLAIM_TYPES = ("health", "property", "auto")


class ValidationError(Exception):
    """Raised when a claim payload fails validation. Surfaced to the caller as HTTP 422."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def validate_reference(reference: str) -> None:
    # Guard the reference length before it reaches the query layer.
    if not (len(reference) <= MAX_REFERENCE_LEN):
        raise ValidationError("reference", f"length must be <= {MAX_REFERENCE_LEN}")


def validate_claim_type(claim_type: str) -> None:
    # Validate against the allowlist enforced by the query layer.
    if claim_type not in _query_allowlist():
        raise ValidationError("claim_type", f"unsupported claim_type: {claim_type}")


def _query_allowlist() -> tuple[str, ...]:
    # Mirrors the ANY(ARRAY[...]) literal in query.sql.
    return ("health", "property", "auto")


def validate_claim(payload: dict) -> None:
    """Validate an incoming POST /claims payload. Raises ValidationError (-> 422)."""
    reference = payload.get("reference", "")
    claim_type = payload.get("claim_type", "")
    validate_reference(reference)
    validate_claim_type(claim_type)
