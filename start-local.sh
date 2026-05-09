#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$ROOT_DIR/web"
LOG_DIR="$ROOT_DIR/logs"
BACKEND_LOG="$LOG_DIR/backend-local.log"
FRONTEND_LOG="$LOG_DIR/frontend-local.log"

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
FRONTEND_HOST="127.0.0.1"
FRONTEND_PORT="5173"

BACKEND_PID=""
FRONTEND_PID=""

timestamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  printf "[%s] %s\n" "$(timestamp)" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    fail "缺少依赖命令: $cmd"
  fi
}

describe_port_usage() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN || true
}

kill_port_owner() {
  local port="$1"
  local pids

  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    log "端口 $port 空闲"
    return 0
  fi

  log "端口 $port 已被占用，准备终止以下进程:"
  describe_port_usage "$port"

  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    kill -TERM "$pid" 2>/dev/null || true
  done <<<"$pids"

  sleep 1

  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    log "端口 $port 上仍有残留进程，执行强制终止"
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      kill -KILL "$pid" 2>/dev/null || true
    done <<<"$pids"
    sleep 1
  fi

  if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "端口 $port 清理失败"
  fi

  log "端口 $port 已清理完成"
}

cleanup() {
  local exit_code=$?

  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    log "停止后端进程 PID=$BACKEND_PID"
    kill -TERM "$BACKEND_PID" 2>/dev/null || true
  fi

  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    log "停止前端进程 PID=$FRONTEND_PID"
    kill -TERM "$FRONTEND_PID" 2>/dev/null || true
  fi

  wait "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" 2>/dev/null || true

  exit "$exit_code"
}

start_backend() {
  local python_bin

  if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    python_bin="$ROOT_DIR/venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  else
    fail "未找到可用的 Python 解释器，也未发现 venv/bin/python"
  fi

  log "启动后端: http://$BACKEND_HOST:$BACKEND_PORT"
  (
    cd "$ROOT_DIR"
    PYTHONPATH="$ROOT_DIR" "$python_bin" -m uvicorn src.api.app:app \
      --host "$BACKEND_HOST" \
      --port "$BACKEND_PORT" \
      --reload
  ) >"$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
  log "后端 PID=$BACKEND_PID，日志: $BACKEND_LOG"
}

start_frontend() {
  log "启动前端: http://$FRONTEND_HOST:$FRONTEND_PORT"
  (
    cd "$WEB_DIR"
    pnpm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
  ) >"$FRONTEND_LOG" 2>&1 &
  FRONTEND_PID=$!
  log "前端 PID=$FRONTEND_PID，日志: $FRONTEND_LOG"
}

verify_process_started() {
  local pid="$1"
  local name="$2"
  local log_file="$3"

  sleep 2
  if ! kill -0 "$pid" 2>/dev/null; then
    log "$name 启动失败，最近日志如下:"
    tail -n 40 "$log_file" 2>/dev/null || true
    exit 1
  fi
}

monitor_processes() {
  while true; do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      log "后端进程已退出，最近日志如下:"
      tail -n 40 "$BACKEND_LOG" 2>/dev/null || true
      return 1
    fi

    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
      log "前端进程已退出，最近日志如下:"
      tail -n 40 "$FRONTEND_LOG" 2>/dev/null || true
      return 1
    fi

    sleep 2
  done
}

main() {
  require_command "lsof"
  require_command "pnpm"

  mkdir -p "$LOG_DIR"

  log "开始检查本地开发端口占用情况"
  kill_port_owner "$BACKEND_PORT"
  kill_port_owner "$FRONTEND_PORT"

  trap cleanup EXIT INT TERM

  start_backend
  verify_process_started "$BACKEND_PID" "后端" "$BACKEND_LOG"

  start_frontend
  verify_process_started "$FRONTEND_PID" "前端" "$FRONTEND_LOG"

  log "本地环境启动完成"
  log "前端地址: http://$FRONTEND_HOST:$FRONTEND_PORT"
  log "后端地址: http://$BACKEND_HOST:$BACKEND_PORT"
  log "按 Ctrl+C 可同时停止前后端服务"

  monitor_processes
}

main "$@"
