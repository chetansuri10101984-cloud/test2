"""
claims-api — HTTP handlers for the claims service.

POST /claims     -> file a claim (validates input, inserts a row)
GET  /claims/{id} -> fetch a claim

Validation failures raise ValidationError, which the framework maps to
HTTP 422 Unprocessable Entity.
"""

from __future__ import annotations

import uuid

from .validation import ValidationError, validate_claim


def file_claim(payload: dict, db) -> tuple[int, dict]:
    """Handle POST /claims. Returns (status_code, body)."""
    try:
        validate_claim(payload)
    except ValidationError as e:
        # Invalid input -> 422. Not a 5xx: the service is working as written.
        return 422, {"error": "validation_failed", "field": e.field, "detail": e.message}

    claim_id = str(uuid.uuid4())
    db.insert_claim(
        claim_id=claim_id,
        claim_type=payload["claim_type"],
        reference=payload["reference"],
    )
    return 201, {"claim_id": claim_id, "status": "filed"}


def get_claim(claim_id: str, db) -> tuple[int, dict]:
    """Handle GET /claims/{id}. Returns (status_code, body)."""
    row = db.get_claim(claim_id)
    if row is None:
        return 404, {"error": "not_found", "claim_id": claim_id}
    return 200, row
