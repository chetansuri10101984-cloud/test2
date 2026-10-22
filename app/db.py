"""
claims-api — thin Postgres data layer.

Talks to the real RDS Postgres (devops-agent-eks-dev-postgres) over the VPC.
Connection params come from env vars injected via a Kubernetes secret. The
app writes claims into the `claims` schema and reads the claim_type allowlist
from `claims.claim_type_allowlist`.
"""

from __future__ import annotations

import os
import uuid

import psycopg
from psycopg.rows import dict_row


def _dsn() -> str:
    return (
        f"host={os.environ['DB_HOST']} "
        f"port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ.get('DB_NAME', 'paymentdb')} "
        f"user={os.environ['DB_USER']} "
        f"password={os.environ['DB_PASSWORD']} "
        f"connect_timeout=5"
    )


class Database:
    """Minimal connection-per-request data access. Fine for a demo service."""

    def query_allowlist(self) -> tuple[str, ...]:
        """Read the claim_type allowlist from the DB (source of truth)."""
        with psycopg.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT claim_type FROM claims.claim_type_allowlist ORDER BY claim_type;")
                return tuple(r[0] for r in cur.fetchall())

    def insert_claim(self, claim_id: str, claim_type: str, reference: str) -> None:
        with psycopg.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO claims.claims (claim_id, claim_type, reference) VALUES (%s, %s, %s);",
                    (claim_id, claim_type, reference),
                )
            conn.commit()

    def get_claim(self, claim_id: str) -> dict | None:
        with psycopg.connect(_dsn()) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT claim_id, claim_type, reference, filed_at FROM claims.claims WHERE claim_id = %s;",
                    (claim_id,),
                )
                row = cur.fetchone()
                if row and row.get("filed_at") is not None:
                    row["filed_at"] = row["filed_at"].isoformat()
                return row

    def healthcheck(self) -> bool:
        try:
            with psycopg.connect(_dsn()) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    cur.fetchone()
            return True
        except Exception:
            return False


def new_claim_id() -> str:
    return str(uuid.uuid4())
