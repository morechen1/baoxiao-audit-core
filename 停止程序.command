#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/runtime/project.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "没有找到本交付包的运行进程。"
  exit 0
fi

PROJECT_PID="$(tr -dc '0-9' < "$PID_FILE")"
if [[ -z "$PROJECT_PID" ]] || ! kill -0 "$PROJECT_PID" 2>/dev/null; then
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
