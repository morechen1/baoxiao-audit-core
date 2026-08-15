#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

for required in \
  README.md \
  RUN_PROJECT.md \
  pyproject.toml \
  uv.lock \
  app/main.py \
  app/services/screening/semantic_parser.py \
  app/services/explanation/validators.py \
  app/services/platform/ingestion.py \
  app/services/platform/screening.py \
  app/services/platform/export.py \
  migrations/env.py \
  scripts/start_project.sh \
  scripts/verify_final_validation.py \
  config/local.env.example \
  release_assets/trusted_data/m7_legacy_current_identity_map.json \
  knowledge_archives/SHA256SUMS; do
  [[ -e "$required" ]] || { echo "缺失：$required" >&2; exit 1; }
done

(
  cd knowledge_archives
  shasum -a 256 -c SHA256SUMS
)

RAW_COUNT="$(find release_assets/trusted_data/raw -type f | wc -l | tr -d ' ')"
PARSED_COUNT="$(find release_assets/trusted_data/parsed_artifacts -type f | wc -l | tr -d ' ')"
[[ "$RAW_COUNT" == "15" ]] || { echo "可信原件数量异常：$RAW_COUNT" >&2; exit 1; }
[[ "$PARSED_COUNT" == "15" ]] || { echo "解析产物数量异常：$PARSED_COUNT" >&2; exit 1; }

PYTHON="$ROOT_DIR/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "缺少 .venv，请先双击一键启动完成依赖安装。" >&2; exit 1; }
"$PYTHON" scripts/verify_final_validation.py
if [[ -d .git ]]; then
  "$PYTHON" scripts/verify_v1_core_integrity.py
fi

SCAN_MODE="source-release"
RUNTIME_CREDENTIAL_READY=false
if [[ -f config/local.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source config/local.env
  set +a
  if [[ -n "${LLM_API_KEY:-}" ]] \
    && [[ -n "${LLM_BASE_URL:-}" ]] \
    && [[ -n "${LLM_MODEL:-}" ]]; then
    SCAN_MODE="competition-runtime"
    RUNTIME_CREDENTIAL_READY=true
  fi
fi
"$PYTHON" scripts/scan_release_secrets.py "$ROOT_DIR" --mode "$SCAN_MODE"

if [[ -f SHA256SUMS ]]; then
  shasum -a 256 -c SHA256SUMS
fi

if [[ "$RUNTIME_CREDENTIAL_READY" == true ]]; then
  PROVIDER_READY="$($PYTHON - <<'PY'
from app.services.explanation import configured_provider_status

print("true" if configured_provider_status().get("ready") else "false")
PY
)"
  [[ "$PROVIDER_READY" == "true" ]] || { echo "Provider：NOT READY" >&2; exit 1; }
  echo "程序完整性：PASS"
  echo "运行凭据：已配置"
  echo "Provider：READY"
else
  echo "程序完整性：PASS"
  echo "运行凭据：未配置（源码发布模式）"
  echo "Provider：NOT CONFIGURED"
fi
