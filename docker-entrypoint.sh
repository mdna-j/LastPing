#!/usr/bin/env bash
set -euo pipefail

# Simple entrypoint: run DB migrations (if alembic present) then exec command
cd "$(dirname "$0")"

# Run alembic upgrade if alembic is available
if command -v alembic >/dev/null 2>&1; then
  echo "Running alembic migrations..."
  alembic upgrade head || true
fi

exec "$@"
