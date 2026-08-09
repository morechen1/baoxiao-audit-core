#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  echo "[保销智审] $*" >&2
  exit 1
}

CONFIG_TEMPLATE="$ROOT_DIR/config/local.env.example"
CONFIG_FILE="$ROOT_DIR/config/local.env"
RUNTIME_DIR="$ROOT_DIR/runtime"

[[ -f "$CONFIG_TEMPLATE" ]] || fail "缺少配置模板 config/local.env.example。"
if [[ ! -f "$CONFIG_FILE" ]]; then
  mkdir -p "$ROOT_DIR/config"
  cp "$CONFIG_TEMPLATE" "$CONFIG_FILE"
  echo "[保销智审] 已创建本机配置 config/local.env（API Key 为空）。"
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

select_python() {
  local candidate
  for candidate in "${BAOXIAO_PYTHON:-}" python3.13 python3.12 python3; do
    [[ -n "$candidate" ]] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

BOOTSTRAP_PYTHON="$(select_python)" \
  || fail "需要 Python 3.12 或更高版本。请安装后重新运行。"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
DEPENDENCY_MARKER="$VENV_DIR/.baoxiao-dependencies-sha256"
DEPENDENCY_SHA="$(shasum -a 256 pyproject.toml uv.lock | shasum -a 256 | awk '{print $1}')"

if [[ ! -x "$PYTHON" ]]; then
  echo "[保销智审] 正在创建本地虚拟环境……"
  "$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR" \
    || fail "虚拟环境创建失败。"
fi

INSTALLED_SHA=""
[[ -f "$DEPENDENCY_MARKER" ]] && INSTALLED_SHA="$(tr -dc 'a-f0-9' < "$DEPENDENCY_MARKER")"
if [[ "$INSTALLED_SHA" != "$DEPENDENCY_SHA" ]]; then
  echo "[保销智审] 正在安装项目依赖（仅首次或依赖变更时执行）……"
  "$PYTHON" -m pip install --upgrade pip \
    || fail "pip 更新失败，请检查网络或 Python 环境。"
  "$PYTHON" -m pip install -e . \
    || fail "依赖安装失败，请检查网络后重试。"
  printf '%s\n' "$DEPENDENCY_SHA" > "$DEPENDENCY_MARKER"
fi

for required in psql pg_isready createdb curl lsof; do
  command -v "$required" >/dev/null 2>&1 \
    || fail "缺少系统命令 ${required}；请安装 PostgreSQL 16 客户端。"
done

export FINAL_DATABASE_HOST="${FINAL_DATABASE_HOST:-127.0.0.1}"
export FINAL_DATABASE_PORT="${FINAL_DATABASE_PORT:-5432}"

if ! pg_isready -h "$FINAL_DATABASE_HOST" -p "$FINAL_DATABASE_PORT" -q; then
  if command -v brew >/dev/null 2>&1; then
    brew services start postgresql@16 >/dev/null 2>&1 \
      || brew services start postgresql >/dev/null 2>&1 \
      || true
  fi
  for _ in $(seq 1 20); do
    pg_isready -h "$FINAL_DATABASE_HOST" -p "$FINAL_DATABASE_PORT" -q && break
    sleep 1
  done
fi
pg_isready -h "$FINAL_DATABASE_HOST" -p "$FINAL_DATABASE_PORT" -q \
  || fail "PostgreSQL 未在 ${FINAL_DATABASE_HOST}:${FINAL_DATABASE_PORT} 就绪。"

export FINAL_DATABASE_NAME="${FINAL_DATABASE_NAME:-baoxiao_contest_final}"
export FINAL_DATABASE_URL="${FINAL_DATABASE_URL:-postgresql+psycopg://${USER}@${FINAL_DATABASE_HOST}:${FINAL_DATABASE_PORT}/${FINAL_DATABASE_NAME}}"
export FINAL_DEMO_PORT="${FINAL_DEMO_PORT:-8000}"
export SEMANTIC_SCREENING_ENABLED=false
export SEMANTIC_PARSER_ENABLED="${SEMANTIC_PARSER_ENABLED:-true}"
export DATA_DIR="${DATA_DIR:-$RUNTIME_DIR/data}"

TRUSTED_TEMPLATE="$ROOT_DIR/release_assets/trusted_data"
if [[ ! -f "$DATA_DIR/m7_legacy_current_identity_map.json" ]]; then
  if [[ -d "$DATA_DIR" ]] && find "$DATA_DIR" -mindepth 1 -print -quit | grep -q .; then
    fail "运行数据目录处于不完整状态：$DATA_DIR；拒绝自动覆盖。"
  fi
  [[ -d "$TRUSTED_TEMPLATE/raw" && -d "$TRUSTED_TEMPLATE/parsed_artifacts" ]] \
    || fail "缺少可信知识恢复资产 release_assets/trusted_data。"
  mkdir -p "$DATA_DIR"
  cp -R "$TRUSTED_TEMPLATE/." "$DATA_DIR/"
fi

if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "[保销智审] 未配置 DeepSeek API Key。系统可以启动，但 Semantic Parser / 受控解释将在 Provider 不可用时 fail closed。"
  echo "[保销智审] 如需完整语义能力，请填写 config/local.env 后重新启动。"
fi

exec "$ROOT_DIR/scripts/start_final_demo.sh"
