#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/runtime/logs"
LOG_FILE="$LOG_DIR/project.log"
PID_FILE="$ROOT_DIR/runtime/project.pid"
PORT="${FINAL_DEMO_PORT:-8888}"

if [[ -f "$ROOT_DIR/config/local.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/config/local.env"
  set +a
  PORT="${FINAL_DEMO_PORT:-8888}"
fi

health_ok() {
  curl --silent --fail --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1
}

open_project() {
  command -v open >/dev/null 2>&1 && open "http://127.0.0.1:${PORT}/"
}

mkdir -p "$LOG_DIR"
if health_ok; then
  echo "保销智审已在运行：http://127.0.0.1:${PORT}/"
  open_project
  exit 0
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 ${PORT} 已被其他程序占用，请先释放端口或修改 config/local.env。" >&2
  exit 1
fi

: > "$LOG_FILE"
nohup "$ROOT_DIR/scripts/start_project.sh" >> "$LOG_FILE" 2>&1 &
PROJECT_PID=$!
printf '%s\n' "$PROJECT_PID" > "$PID_FILE"

cleanup_pid_file() {
  if [[ -f "$PID_FILE" ]] && [[ "$(tr -dc '0-9' < "$PID_FILE")" == "$PROJECT_PID" ]]; then
    rm -f "$PID_FILE"
  fi
}
finish() {
  local status=$?
  cleanup_pid_file
  if [[ "$status" -ne 0 && -t 0 ]]; then
    echo
    read -r -p "启动未完成。请查看上方错误，按回车键关闭窗口……" _ || true
  fi
}
trap finish EXIT

echo "正在准备依赖、数据库与可信知识，请稍候……"
for _ in $(seq 1 600); do
  if health_ok; then
    echo "保销智审启动成功：http://127.0.0.1:${PORT}/"
    echo "运行日志：${LOG_FILE}"
    open_project
    wait "$PROJECT_PID"
    exit $?
  fi
  if ! kill -0 "$PROJECT_PID" 2>/dev/null; then
    echo "启动失败，最后 100 行日志如下：" >&2
    tail -n 100 "$LOG_FILE" >&2
    exit 1
  fi
  sleep 1
done

echo "启动超时，最后 100 行日志如下：" >&2
tail -n 100 "$LOG_FILE" >&2
exit 1
