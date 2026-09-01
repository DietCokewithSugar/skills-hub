#!/usr/bin/env bash
# 同容器起 API 与 worker 两个进程。
#
# 任一进程退出就整体退出（`wait -n`），让平台重启整个容器 —— 否则会出现
# 「API 还活着但 worker 已经死了」这种最难查的状态：页面正常、提交有反应、
# 执行永远停在 queued。
set -euo pipefail

API_PID=""
WORKER_PID=""

shutdown() {
  trap - TERM INT
  [ -n "$API_PID" ] && kill -TERM "$API_PID" 2>/dev/null || true
  [ -n "$WORKER_PID" ] && kill -TERM "$WORKER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap shutdown TERM INT

mkdir -p "${BENCH_WORKSPACE_ROOT:-/data/workspaces}"

echo "[bench] 启动 worker"
arq bench.worker.tasks.WorkerSettings &
WORKER_PID=$!

echo "[bench] 启动 API（:${PORT:-8000}）"
uvicorn bench.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
API_PID=$!

# 谁先退出就带着整个容器一起退
wait -n
code=$?
echo "[bench] 有进程退出（code=$code），关闭容器"
shutdown
exit "$code"
