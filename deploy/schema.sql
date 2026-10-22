-- claims-api database schema. Isolated in its own `claims` schema.

CREATE SCHEMA IF NOT EXISTS claims;

CREATE TABLE IF NOT EXISTS claims.claim_type_allowlist (
    claim_type TEXT PRIMARY KEY
);

-- The claim types the service accepts.
INSERT INTO claims.claim_type_allowlist (claim_type)
VALUES ('health'), ('property'), ('auto')
ON CONFLICT (claim_type) DO NOTHING;

CREATE TABLE IF NOT EXISTS claims.claims (
    claim_id   TEXT PRIMARY KEY,
    claim_type TEXT NOT NULL,
    reference  TEXT NOT NULL,
    filed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
