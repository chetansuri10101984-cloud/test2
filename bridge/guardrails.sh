#!/usr/bin/env bash
# Guardrails: validate that headless Kiro changed only the allowed files with a
# small, syntactically valid diff. Abort the build (exit 1) on any violation.
set -euo pipefail

# Ignore file-mode-only changes (the buildspec chmod +x's helper scripts, which
# would otherwise show up as modifications). We only care about content edits.
git config core.fileMode false

CHANGED="$(git diff --name-only)"
echo "Changed files:"
echo "$CHANGED"

N="$(echo "$CHANGED" | grep -c . || true)"
if [ "$N" -eq 0 ]; then
  echo "GUARDRAIL: Kiro made no changes"
  exit 1
fi
if [ "$N" -gt 2 ]; then
  echo "GUARDRAIL: too many files changed ($N > 2)"
  exit 1
fi

for f in $CHANGED; do
  case "$f" in
    app/validation.py|app/query.sql) : ;;
    *) echo "GUARDRAIL: protected/unexpected file modified: $f"; exit 1 ;;
  esac
done

for f in $CHANGED; do
  case "$f" in
    *.py) python3 -m py_compile "$f" || { echo "GUARDRAIL: py_compile failed on $f"; exit 1; } ;;
  esac
done

echo "Guardrails passed."
