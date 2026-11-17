#!/usr/bin/env bash
# The verification gate. Same command locally, in the Claude Stop hook, and in CI.
#   scripts/check.sh          lint + types + all Python tests (~5 s)
#   scripts/check.sh --quick  lint + types only
# Android checks run in CI only (the Android SDK can't be downloaded in cloud containers):
#   cd android && ./gradlew -Pmos.coreOnly :core:test
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}

echo "== ruff" && $PY -m ruff check .
echo "== mypy" && $PY -m mypy
if [[ "${1:-}" != "--quick" ]]; then
  echo "== pytest" && $PY -m pytest -q
fi
echo "check.sh: all green"
