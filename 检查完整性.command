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

python3 scripts/verify_final_validation.py
python3 scripts/scan_release_secrets.py "$ROOT_DIR"

if [[ -f SHA256SUMS ]]; then
  shasum -a 256 -c SHA256SUMS
fi

echo "完整性检查通过：源码、依赖描述、可信知识资产、冻结验证资产与秘密扫描均通过。"
