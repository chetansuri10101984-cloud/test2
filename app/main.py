"""
claims-api — HTTP entrypoint (Flask).

Routes:
    GET  /healthz        -> liveness/readiness. Checks process + DB connectivity.
    POST /claims         -> file a claim. 201 on success, 422 on validation failure.
    GET  /claims/<id>    -> fetch a claim. 200 / 404.

Every request is logged as a single JSON line to stdout (shipped to CloudWatch
by the cluster's log pipeline): timestamp, path, method, status, latency_ms,
ref_len, claim_type, version.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from . import __version__
from .db import Database, new_claim_id
from .validation import ValidationError, validate_claim

app = Flask(__name__)
db = Database()


def _log(path: str, method: str, status: int, latency_ms: float, ref_len: int, claim_type: str) -> None:
    """Emit one structured JSON access-log line to stdout."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "status": status,
        "latency_ms": round(latency_ms, 2),
        "ref_len": ref_len,
        "claim_type": claim_type,
        "version": __version__,
    }
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


@app.route("/healthz", methods=["GET"])
def healthz():
    # Liveness is process-level only on purpose: it does NOT exercise the
    # validation path, which is why the pod stays green during the incident.
    ok = db.healthcheck()
    return (jsonify({"status": "ok" if ok else "degraded", "version": __version__}),
            200 if ok else 503)


@app.route("/claims", methods=["POST"])
def post_claims():
    t0 = time.time()
    payload = request.get_json(silent=True) or {}
    reference = payload.get("reference", "")
    claim_type = payload.get("claim_type", "")
    ref_len = len(reference) if isinstance(reference, str) else 0

    try:
        validate_claim(payload)
    except ValidationError as e:
        latency = (time.time() - t0) * 1000
        _log("/claims", "POST", 422, latency, ref_len, claim_type)
        return jsonify({"error": "validation_failed", "field": e.field, "detail": e.message}), 422

    claim_id = new_claim_id()
    try:
        db.insert_claim(claim_id=claim_id, claim_type=claim_type, reference=reference)
    except Exception as e:  # genuine infra failure would be a 5xx — not what this incident is
        latency = (time.time() - t0) * 1000
        _log("/claims", "POST", 500, latency, ref_len, claim_type)
        return jsonify({"error": "internal_error", "detail": str(e)}), 500

    latency = (time.time() - t0) * 1000
    _log("/claims", "POST", 201, latency, ref_len, claim_type)
    return jsonify({"claim_id": claim_id, "status": "filed"}), 201


@app.route("/claims/<claim_id>", methods=["GET"])
def get_claims(claim_id: str):
    t0 = time.time()
    row = db.get_claim(claim_id)
    status = 200 if row is not None else 404
    latency = (time.time() - t0) * 1000
    _log(f"/claims/{claim_id}", "GET", status, latency, 0, "")
    if row is None:
        return jsonify({"error": "not_found", "claim_id": claim_id}), 404
    return jsonify(row), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
