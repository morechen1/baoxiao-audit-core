#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="$ROOT_DIR/.venv/bin/python"
DEMO_PORT="${DEMO_PORT:-8000}"
DEMO_DATABASE_NAME="${DEMO_DATABASE_NAME:-baoxiao_demo}"
DEMO_DATABASE_URL="${DEMO_DATABASE_URL:-postgresql+psycopg://${USER}@localhost:5432/${DEMO_DATABASE_NAME}}"

fail() {
  echo "[demo] $*" >&2
  exit 1
}

[[ -x "$PYTHON" ]] || fail "Missing .venv. Run 'make install' first."
command -v psql >/dev/null || fail "PostgreSQL client 'psql' is required for the local Demo runtime."
command -v pg_isready >/dev/null || fail "PostgreSQL readiness tool 'pg_isready' is required."

[[ "$DEMO_DATABASE_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] \
  || fail "DEMO_DATABASE_NAME must be a PostgreSQL identifier."

case "$DEMO_DATABASE_URL" in
  */"$DEMO_DATABASE_NAME"|*/"$DEMO_DATABASE_NAME"\?*) ;;
  *) fail "DEMO_DATABASE_URL must target the isolated database $DEMO_DATABASE_NAME." ;;
esac

if [[ "$DEMO_DATABASE_NAME" == "baoxiao_contest_final" ]]; then
  echo "[demo] Final database requested; routing to the trusted final-demo launcher."
  export FINAL_DATABASE_NAME="$DEMO_DATABASE_NAME"
  export FINAL_DATABASE_URL="$DEMO_DATABASE_URL"
  export FINAL_DEMO_PORT="$DEMO_PORT"
  export SEMANTIC_SCREENING_ENABLED=false
  exec "$ROOT_DIR/scripts/start_final_demo.sh"
fi

export DATABASE_URL="$DEMO_DATABASE_URL"
export DATA_DIR="${DATA_DIR:-$ROOT_DIR/.demo-data}"
export BAOXIAO_DEMO_RUNTIME=1
export DEMO_DATABASE_NAME

if ! pg_isready -h 127.0.0.1 -p 5432 -q; then
  if command -v brew >/dev/null && brew services start postgresql@16 >/dev/null 2>&1; then
    for _ in {1..10}; do
      pg_isready -h 127.0.0.1 -p 5432 -q && break
      sleep 1
    done
  fi
fi
pg_isready -h 127.0.0.1 -p 5432 -q || fail "PostgreSQL is not ready on 127.0.0.1:5432. Start postgresql@16 and retry."

if ! psql -h 127.0.0.1 -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname = '$DEMO_DATABASE_NAME'" | grep -qx "1"; then
  echo "[demo] Creating isolated database $DEMO_DATABASE_NAME..."
  createdb -h 127.0.0.1 "$DEMO_DATABASE_NAME" || fail "Could not create $DEMO_DATABASE_NAME. Check your local PostgreSQL role."
fi

if lsof -nP -iTCP:"$DEMO_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port $DEMO_PORT is already in use. Set DEMO_PORT to another local port and retry."
fi

echo "[demo] Applying migrations to isolated $DEMO_DATABASE_NAME..."
"$PYTHON" -m alembic upgrade head
echo "[demo] Preparing constructed contest fixture (never formal evidence)..."
"$PYTHON" scripts/demo_seed.py
echo "[demo] Running API and controlled-RAG preflight..."
"$PYTHON" scripts/demo_smoke.py
echo "[demo] Ready: http://127.0.0.1:$DEMO_PORT"
echo "[demo] Use the three prebuilt cases for stable presentation, or submit live text through the workspace."
exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$DEMO_PORT"
