#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/runtime/logs"
LOG_FILE="$LOG_DIR/project.log"
PID_FILE="$ROOT_DIR/runtime/project.pid"
CONFIG_FILE="$ROOT_DIR/config/local.env"
CONFIG_SHA_FILE="$ROOT_DIR/runtime/config.sha256"
PORT="${FINAL_DEMO_PORT:-8888}"

if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$CONFIG_FILE"
  set +a
  PORT="${FINAL_DEMO_PORT:-8888}"
fi

CURRENT_CONFIG_SHA="missing"
if [[ -f "$CONFIG_FILE" ]]; then
  CURRENT_CONFIG_SHA="$(shasum -a 256 "$CONFIG_FILE" | awk '{print $1}')"
fi

health_ok() {
  curl --silent --fail --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1
}

open_project() {
  command -v open >/dev/null 2>&1 && open "http://127.0.0.1:${PORT}/"
}

owned_project_pid() {
  local candidate_pid=""
  local working_dir=""
  if [[ -f "$PID_FILE" ]]; then
    candidate_pid="$(tr -dc '0-9' < "$PID_FILE")"
  fi
  if [[ -z "$candidate_pid" ]] || ! kill -0 "$candidate_pid" 2>/dev/null; then
    candidate_pid="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  fi
  [[ -n "$candidate_pid" ]] && kill -0 "$candidate_pid" 2>/dev/null || return 1
  working_dir="$(lsof -a -p "$candidate_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  [[ "$working_dir" == "$ROOT_DIR" ]] || return 1
  printf '%s\n' "$candidate_pid"
}

stop_owned_project() {
  local project_pid="$1"
  kill "$project_pid"
  for _ in $(seq 1 20); do
    if ! lsof -a -p "$project_pid" -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      rm -f "$PID_FILE"
      return 0
    fi
    sleep 1
  done
  echo "旧进程未在 20 秒内退出，请双击停止程序后重试。" >&2
  return 1
}

mkdir -p "$LOG_DIR"
if health_ok; then
  RUNNING_CONFIG_SHA=""
  [[ -f "$CONFIG_SHA_FILE" ]] && RUNNING_CONFIG_SHA="$(tr -dc 'a-f0-9' < "$CONFIG_SHA_FILE")"
  if [[ "$RUNNING_CONFIG_SHA" == "$CURRENT_CONFIG_SHA" ]]; then
    echo "保销智审已在运行：http://127.0.0.1:${PORT}/"
    open_project
    exit 0
  fi
  RUNNING_PID="$(owned_project_pid || true)"
  if [[ -z "$RUNNING_PID" ]]; then
    echo "检测到配置已变更，但端口 ${PORT} 上的进程不属于当前交付包；拒绝自动结束。" >&2
    exit 1
  fi
  echo "检测到本机配置已变更，正在重启以安全加载新配置……"
  stop_owned_project "$RUNNING_PID"
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
  if [[ -f "$PID_FILE" ]] \
    && [[ "$(tr -dc '0-9' < "$PID_FILE")" == "$PROJECT_PID" ]] \
    && ! kill -0 "$PROJECT_PID" 2>/dev/null; then
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
    printf '%s\n' "$CURRENT_CONFIG_SHA" > "$CONFIG_SHA_FILE"
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
