#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/runtime/project.pid"
PORT="${FINAL_DEMO_PORT:-8888}"

if [[ -f "$ROOT_DIR/config/local.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/config/local.env"
  set +a
  PORT="${FINAL_DEMO_PORT:-8888}"
fi

PROJECT_PID=""
[[ -f "$PID_FILE" ]] && PROJECT_PID="$(tr -dc '0-9' < "$PID_FILE")"
if [[ -z "$PROJECT_PID" ]] || ! kill -0 "$PROJECT_PID" 2>/dev/null; then
  PROJECT_PID="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
fi
if [[ -n "$PROJECT_PID" ]] && kill -0 "$PROJECT_PID" 2>/dev/null; then
  WORKING_DIR="$(lsof -a -p "$PROJECT_PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  if [[ "$WORKING_DIR" != "$ROOT_DIR" ]]; then
    echo "端口 ${PORT} 上的进程不属于当前交付包，拒绝结束。" >&2
    exit 1
  fi
else
  rm -f "$PID_FILE"
  echo "程序已经停止。"
  exit 0
fi

kill "$PROJECT_PID"
for _ in $(seq 1 20); do
  if ! kill -0 "$PROJECT_PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "保销智审已安全停止。"
    exit 0
  fi
  sleep 1
done

echo "进程未在 20 秒内退出，请查看 runtime/logs/project.log。" >&2
exit 1
