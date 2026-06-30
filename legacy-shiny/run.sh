#!/usr/bin/env bash
# 启动 v0.6 单体 Shiny (冻结快照)。
# 必须在本目录运行 —— global.R / .Rprofile / renv 全部依赖相对路径。
set -e
cd "$(dirname "$0")"
PORT="${PORT:-20035}"
echo "[legacy v0.6] 启动 Shiny 于 127.0.0.1:${PORT} (需 renv::restore() 已完成)"
Rscript -e "shiny::runApp(host='127.0.0.1', port=${PORT})"
