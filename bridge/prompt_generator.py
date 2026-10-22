#!/usr/bin/env python3
"""
Convert an AWS DevOps Agent mitigation payload into a Kiro CLI remediation
prompt.

The payload is the JSON written by the extract Lambda: it contains the
mitigation summary (markdown produced by the DevOps Agent investigation /
mitigation) plus identifying metadata. We do NOT prescribe the fix here — the
remediation steering file (.kiro/steering/remediation-agent.md) constrains what
Kiro may change. This prompt simply hands Kiro the agent's own findings and
tells it to apply the minimal fix.

Usage: python3 bridge/prompt_generator.py <mitigation.json>
"""

from __future__ import annotations

import json
import sys


def _extract_summary(payload: dict) -> str:
    # The extract Lambda stores the mitigation summary markdown under
    # "mitigation_summary". Fall back gracefully to other shapes.
    for key in ("mitigation_summary", "summary", "mitigation_summary_md"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            # e.g. {"action": "...", "reasoning": "..."}
            parts = [f"{k}: {v}" for k, v in val.items() if v]
            if parts:
                return "\n".join(parts)
    # Last resort: hand over the whole payload so Kiro still has context.
    return json.dumps(payload, indent=2)


def build_prompt(payload: dict) -> str:
    summary = _extract_summary(payload)
    task_id = payload.get("task_id") or payload.get("metadata", {}).get("task_id", "")
    exec_id = payload.get("execution_id") or payload.get("metadata", {}).get("execution_id", "")

    return f"""You are remediating a production incident on the claims-api service.

An AWS DevOps Agent investigation has completed and produced the mitigation
plan below. Apply the minimal code fix that resolves the root cause it
describes, following the constraints in the remediation-agent steering file
(you may change only app/validation.py and/or app/query.sql, smallest possible
diff, no new files, no refactoring).

--- AWS DevOps Agent mitigation plan (investigation {task_id}, execution {exec_id}) ---
{summary}
--- end mitigation plan ---

Steps:
1. Read app/validation.py and app/query.sql.
2. Map the mitigation plan to the reference-length boundary fix and/or the
   claim_type allowlist fix described in the steering file.
3. Apply the minimal edit(s) so that a 64-character reference is accepted and
   the claim_type allowlist in both files matches the ALLOWED_CLAIM_TYPES
   constant.
4. Do not touch any other file. Do not change the version string. Stop when the
   fix is applied; the pipeline handles the commit and pull request.
"""


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: prompt_generator.py <mitigation.json>\n")
        return 2
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    sys.stdout.write(build_prompt(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
