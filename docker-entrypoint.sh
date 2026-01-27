#!/usr/bin/env bash
set -euo pipefail

# Simple entrypoint: run DB migrations (if alembic present) then exec command
cd "$(dirname "$0")"

# Run bootstrap + alembic upgrade if alembic is available (retry until DB is ready)
if [ "${RUN_MIGRATIONS:-1}" = "1" ] && command -v alembic >/dev/null 2>&1; then
  if [ "${BOOTSTRAP_DB:-1}" = "1" ] && [ -f /app/scripts/bootstrap_db.py ]; then
    echo "Bootstrapping database (if empty)..."
    attempts=0
    until python /app/scripts/bootstrap_db.py; do
      attempts=$((attempts + 1))
      if [ "$attempts" -ge 30 ]; then
        echo "Database bootstrap failed after ${attempts} attempts."
        break
      fi
      echo "Bootstrap failed; retrying in 2s..."
      sleep 2
    done
  fi

  echo "Running alembic migrations..."
  attempts=0
  until alembic upgrade head; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      echo "Alembic migration failed after ${attempts} attempts."
      break
    fi
    echo "Alembic failed; retrying in 2s..."
    sleep 2
  done
fi

exec "$@"
