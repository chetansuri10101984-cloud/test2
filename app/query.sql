-- claims-api — claim_type allowlist check.
-- Confirms the submitted claim_type is one the service accepts.

SELECT EXISTS (
    SELECT 1
    FROM claim_type_allowlist
    WHERE claim_type = :claim_type
      AND claim_type = ANY (ARRAY['health', 'property'])
) AS is_allowed;
