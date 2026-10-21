#!/usr/bin/env bash
# Start the backend (:8000) and frontend (:5173) together. Stop both with Ctrl+C.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$ROOT/backend/venv" ]; then
  echo "Creating backend virtualenv..."
  python3 -m venv "$ROOT/backend/venv"
  "$ROOT/backend/venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "Installing frontend dependencies..."
  (cd "$ROOT/frontend" && pnpm install)
fi
if [ ! -f "$ROOT/backend/.env" ]; then
  echo "Note: backend/.env is missing (copy backend/.env.example and set YNAB_API_KEY)." >&2
fi

echo "Applying database migrations..."
(cd "$ROOT/backend" && ./venv/bin/alembic upgrade head)

pids=()
cleanup() {
  trap - INT TERM EXIT
  echo; echo "Stopping..."
  for pid in "${pids[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# setsid: own session (stoppable as a group, never suspended by the terminal); stdin detached
(cd "$ROOT/backend" && exec setsid ./venv/bin/fastapi dev src/main.py < /dev/null) &
pids+=($!)
(cd "$ROOT/frontend" && exec setsid pnpm run dev < /dev/null) &
pids+=($!)

echo "Backend:  http://localhost:8000  (docs at /docs)"
echo "Frontend: http://localhost:5173"
# Exit as soon as either server stops
wait -n
