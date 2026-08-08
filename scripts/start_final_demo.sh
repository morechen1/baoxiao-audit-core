#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="$ROOT_DIR/.venv/bin/python"
ALEMBIC="$ROOT_DIR/.venv/bin/alembic"
FINAL_PORT="${FINAL_DEMO_PORT:-8000}"
FINAL_DATABASE_NAME="${FINAL_DATABASE_NAME:-baoxiao_contest_final}"
FINAL_DATABASE_URL="${FINAL_DATABASE_URL:-postgresql+psycopg://${USER}@localhost:5432/${FINAL_DATABASE_NAME}}"

export DATABASE_URL="$FINAL_DATABASE_URL"
export DATA_DIR="${DATA_DIR:-$ROOT_DIR/.final-demo-data}"
export BAOXIAO_FINAL_RUNTIME=1
export FINAL_DATABASE_NAME
export SEMANTIC_SCREENING_ENABLED=false

fail() {
  echo "[final-demo] $*" >&2
  exit 1
}

[[ "$FINAL_DATABASE_NAME" == "baoxiao_contest_final" ]] \
  || fail "FINAL_DATABASE_NAME must remain baoxiao_contest_final."
case "$FINAL_DATABASE_URL" in
  */"$FINAL_DATABASE_NAME"|*/"$FINAL_DATABASE_NAME"\?*) ;;
  *) fail "FINAL_DATABASE_URL must target $FINAL_DATABASE_NAME." ;;
esac
[[ -x "$PYTHON" && -x "$ALEMBIC" ]] || fail "Missing .venv; run make install first."
command -v psql >/dev/null || fail "PostgreSQL client psql is required."
command -v pg_isready >/dev/null || fail "PostgreSQL pg_isready is required."
pg_isready -h 127.0.0.1 -p 5432 -q || fail "PostgreSQL is not ready on 127.0.0.1:5432."

if ! psql -h 127.0.0.1 -d postgres -Atqc \
  "SELECT 1 FROM pg_database WHERE datname = '$FINAL_DATABASE_NAME'" | grep -qx "1"; then
  echo "[final-demo] Creating $FINAL_DATABASE_NAME..."
  createdb -h 127.0.0.1 "$FINAL_DATABASE_NAME"
fi

echo "[final-demo] Applying migrations..."
"$ALEMBIC" upgrade head

document_count="$(psql -h 127.0.0.1 -d "$FINAL_DATABASE_NAME" -Atqc 'SELECT count(*) FROM source_documents')"
if [[ "$document_count" == "0" ]]; then
  echo "[final-demo] Restoring the reviewed 15-source trusted corpus..."
  "$PYTHON" scripts/rematerialize_m7_trusted_knowledge.py \
    --database-url "$FINAL_DATABASE_URL" \
    --data-dir "$DATA_DIR"
elif [[ "$document_count" != "15" ]]; then
  fail "Expected 15 SourceDocuments, found $document_count; refusing automatic repair."
fi

echo "[final-demo] Verifying 73-chunk runtime and three deterministic cases..."
"$PYTHON" scripts/final_demo_smoke.py --skip-explanations

if lsof -nP -iTCP:"$FINAL_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port $FINAL_PORT is already in use."
fi

echo "[final-demo] Ready: http://127.0.0.1:$FINAL_PORT"
echo "[final-demo] Database: $FINAL_DATABASE_NAME; semantic screening: disabled"
exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$FINAL_PORT"
