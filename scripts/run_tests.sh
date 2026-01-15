#!/usr/bin/env bash
# Run tests using local venv if present, else fallback to system python
set -euo pipefail
if [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest -q
else
  python3 -m pytest -q
fi
