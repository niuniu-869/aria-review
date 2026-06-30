# Aria Review · Monorepo (三服务再架构)

> 状态: 三服务架构 (agent / web / r-analysis) 已端到端打通。快速启动与验证命令见 `README.md`。
> v0.6 Shiny 已迁入 `legacy-shiny/` 作为历史快照保留。

## 布局
```
packages/contracts/   OpenAPI 单一真源 (前后端类型由它生成)
services/r-analysis/   R + plumber + bibliometrix (分析内核, 内部服务)
services/agent/        Python + FastAPI (唯一后端: 代理/编排/调R)
apps/web/              Vite + React + TS (纯视图)
docker-compose.yml     三服务本地编排
```

## 数据流（核心链路）
```
浏览器 ──upload──▶ agent /projects/{p}/corpus ──multipart──▶ r-analysis /parse ──▶ 语料(RDS, 状态机)
浏览器 ──poll────▶ agent /corpus/{id} ─────────────────────▶ r-analysis /corpus/{id}
浏览器 ──overview▶ agent /corpus/{id}/overview ────────────▶ r-analysis /overview ──bibliometrix──▶ 统计 JSON
```

## 本地开发 (不用 docker, 最快)
```bash
# 1. r-analysis (需 R + plumber/bibliometrix)
#    注: bibliometrix 现仅在 v0.6 的 renv 库里 (v0.6 已移入 legacy-shiny/)。本地跑需把它放上 libpath:
#    RLIB=legacy-shiny/renv/library/R-4.3/x86_64-pc-linux-gnu   (按你的 R 版本/架构调整)
#    或直接用 docker (镜像自带 bibliometrix)。待办: 给 r-analysis 独立 renv 以解耦。
R_LIBS=legacy-shiny/renv/library/R-4.3/x86_64-pc-linux-gnu \
  PORT=8001 Rscript -e 'setwd("services/r-analysis"); library(plumber); plumb("plumber.R")$run(host="0.0.0.0", port=8001)'
# 2. agent (需 fastapi/uvicorn/httpx)
R_ANALYSIS_URL=http://localhost:8001 uvicorn app.main:app --app-dir services/agent --port 8000 --reload
# 3. web
pnpm -C apps/web install && pnpm -C apps/web gen:api && pnpm -C apps/web dev   # http://localhost:5173
```

## docker
```bash
docker compose up --build              # web :8080, agent :8000, r-analysis (内部), postgres 127.0.0.1:${POSTGRES_PORT:-55432}
```

## 测试
```bash
# r-analysis 测试需 bibliometrix 在 libpath (见上方 R_LIBS 说明)
R_LIBS=legacy-shiny/renv/library/R-4.3/x86_64-pc-linux-gnu \
  Rscript -e 'testthat::test_dir("services/r-analysis/tests/testthat")'  # 30
cd services/agent && \
  DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn \
  TEST_DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn_test \
  python3 -m pytest -q
pnpm -C apps/web test                                                    # 5
```

## 公开仓库边界
- 本地演示材料、验证截图、benchmark 输出、demo 语料和测试数据文件不进入公开提交。
- 离线 demo 使用 `services/agent/scripts/run_agent_e2e.py --offline-fixtures builtin` 的内置样例。
- 本地运行产生的 `reviews_output/`、`bench_search/`、`data/`、截图和报告文件由 `.gitignore` 保留在本机。
