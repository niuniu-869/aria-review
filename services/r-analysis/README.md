# services/r-analysis

bibliometrix 分析内核, 以 plumber HTTP 服务暴露 (D4)。**仅供 agent 后端内部调用**, 不直接面向前端。

## 结构
- `R/analysis.R` — 纯分析函数 (移植自 legacy `R/fct_analysis.R`), 返回与 `packages/contracts` 对齐的 JSON-able list (Codex step1-P1: 契约 ≠ R 对象)。
- `R/store.R` — 语料存取 + 状态机 (parsing/ready/failed), 原子写 (Codex #5), RDS 仅系统产物 (Codex #18)。
- `plumber.R` — 薄 HTTP 层: `/healthz` `/parse` `/corpus/<id>` `/corpus/<id>/overview`。
- `entrypoint.R` — 启动。

## 端点
| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | /healthz | 存活 |
| POST | /parse (multipart: file + dbsource) | 解析→存储, 返回 corpus meta |
| GET  | /corpus/{id} | 语料状态 |
| GET  | /corpus/{id}/overview | 概览 (仅 ready) |

## 本地运行
```bash
# 装依赖 (一次)
R -q -e "install.packages(c('plumber','bibliometrix','jsonlite'))"
# 启动
PORT=8001 Rscript entrypoint.R
```

## 测试 (不需要 plumber)
```bash
# 从仓库根。注: bibliometrix 现在 v0.6 的 renv 库里 (v0.6 已移入 legacy-shiny/),
# 本地跑需把它放上 libpath; 或用 docker (镜像自带)。
R_LIBS=legacy-shiny/renv/library/R-4.3/x86_64-pc-linux-gnu \
  Rscript -e 'testthat::test_dir("services/r-analysis/tests/testthat")'   # 30 pass
```
纯函数 (analysis/store) 全部可测; plumber HTTP 层薄, 靠集成测试覆盖。

## 待办 (设计 §12)
- RDS → parquet + 明确 schema (RDS 绑 R 版本)。
- 内存态 plumber + LRU (Codex #3); 现为每请求磁盘载入。
- 其余 7 个分析端点 (sources/authors/.../prisma)。
