# BiblioCN 三服务再架构设计文档

> 状态: 规划中 (2026-05-21)
> 决策来源: /plan-eng-review 交互式架构会话
> 取代: v0.6 单体 Shiny 架构 (冻结保留为参照真值)

---

## 0. 决策摘要 (已与用户确认)

| 编号 | 决策 | 选择 | 理由 |
|------|------|------|------|
| D2 | 是否换语言 | **拆三语言** | Shiny 撑不起 agent + 富前端的产品愿景 |
| D2b | 迁移策略 | **全量规划重写** (非 strangler-fig) | 用户决定; 已知大爆炸风险并接受 |
| D3 | 仓库拓扑 | **Monorepo** | 单人 + Claude Code: 单一上下文、跨服务原子提交 |
| D4 | R 分析边界 | **plumber 常驻 HTTP 服务** | 交互式分析需进程温热 + 语料常驻, 避免冷启动 |
| D5 | 前端 / 职责 | **Vite + React SPA + 纯 Python(FastAPI)后端** | 边界最干净: TS 只管视图, Python 唯一后端 |
| D6 | 构建顺序 | **垂直切片优先** | 第一周打通端到端、验证接缝, 风险前置 |

**安全阀 (标准做法, 非可选)**: v0.6 冻结但保持可运行, 作为新栈的逐功能参照真值与回退兜底,
直到新栈达到功能对等才退役。

---

## 1. 目标架构

```
apps/web  (Vite + React + TypeScript)                    [视图层]
  · 8 个分析页 + AI/agent 对话 + PRISMA/报告导出
  · 服务端态: TanStack Query   本地 UI 态: Zustand
  · 流式: EventSource 接 SSE   markdown 安全渲染 (移植 fct_markdown 白名单)
        │ REST/JSON  +  SSE(流式)
        ▼
services/agent  (Python + FastAPI)                       [唯一后端]
  · API 网关/BFF: 所有前端请求入口
  · agent 编排: LLM 调用、工具调用、长任务队列
  · 鉴权 / 会话 / 用户自带 key (沿用 v0.6 会话级不落盘思路)
  · 持久化: Postgres(用户/项目/对话/任务) + 对象存储或 FS(语料 parquet, 产物图)
        │ httpx (REST/JSON)              │ LLM API
        ▼                               ▼
services/r-analysis (R + plumber)         LLM (DeepSeek / Claude)
  · 温热 worker 池, bibliometrix 常驻已加载
  · 端点: /parse /overview /sources /authors /documents
          /conceptual /intellectual /social /prisma
  · 语料持久化: parse 一次 → parquet/rds, 各分析端点按 corpus_id 载入
  · 产物图: 存对象存储传 URL (不内联 base64 巨串)
```

接缝设计要点:
- **契约单一真源**: `packages/contracts/` 放 OpenAPI/JSON schema, 三端共享。多服务最大的隐性风险是契约漂移, 用契约测试在 CI 钉死。
- **产物按端点定形 (Codex #6 修正)**: 不是一律"存 URL"。静态图→PNG URL; 交互网络 (visNetwork/igraph)→nodes/edges DTO; 三字段图→plotly JSON; 大表→分页。详见 §12。
- **长分析异步化**: 共被引/耦合在大语料下数十秒, Python→R 不能同步阻塞, 走任务 + SSE/轮询进度。

---

## 2. 仓库布局 (Monorepo)

```
biblio_cn/                     (沿用现仓库; tag v0.6-shiny 后重构 master)
├── apps/
│   └── web/                   Vite + React + TS 前端
├── services/
│   ├── agent/                 FastAPI + agent 编排 (Python, greenfield)
│   └── r-analysis/            plumber + bibliometrix (R, 移植 fct_ 分析层)
├── packages/
│   └── contracts/             OpenAPI/JSON schema (三端共享契约, single source of truth)
├── legacy-shiny/              现 v0.6 Shiny app 整体移入, 冻结但可 docker 起来对照
├── docker-compose.yml         本地一键起全栈 (web + agent + r-analysis + postgres)
├── docs/
└── .github/workflows/         CI 按子目录分流 (r: testthat / py: pytest / ts: vitest)
```

---

## 3. 复用资产盘点 ("不重建已工作的东西")

> 核心结论: 真正从零重写的是 `mod_` 响应式层 (~2206 行) + Shiny UI, **不是全部 9k 行**。
>
> ⚠️ **Codex 二审修正 (见 §12)**: `fct_` 分析层并非全部"近乎原样"——返回 igraph/plotly/ggplot/matrix
> 对象的函数 (`fct_analysis.R:195/213/227`) 必须逐端点设计 JSON DTO; 只有返回纯统计的函数能近乎直接移植。

| 现有代码 | 去向 | 改动程度 |
|---------|------|---------|
| `fct_analysis/prisma/dedup/crossref/openalex_to_corpus/pubmed/demo_data` | → services/r-analysis (留 R) | **近乎原样**, 连 `test-fct_*` 测试复用 |
| `fct_llm*/prompts/review_templates/cite_check/cost/context` | → services/agent (移植到 Python) | 逻辑 + 提示词/抗幻觉约束作为资产移植 |
| `fct_markdown` (XSS 白名单 sanitizer) | → 前端 (或 Python) | 重实现, 规则照搬 |
| `fct_session_key` (用户自带 key 不落盘) | → agent 设计沿用 | 模式复用 |
| `mod_*.R` (20 模块, 响应式) | → React 组件 + FastAPI 端点 | **真正重写** (逻辑 re-express) |
| `app.R / global.R / ui_helpers.R` | → 前端脚手架 + Python 启动 | 重写 |
| 487 测试 | `test-fct_*` 大批存活; `test-mod_*` 不迁 | 部分复用 |

注: `global.R` 注释提到的 `lit_pipeline.py` 仓库中不存在 → Python 服务无现成种子, greenfield 起步。

---

## 4. 垂直切片 (D6)

两条切片覆盖两个最危险的接缝:

**切片 1 — 上传语料 → 领域概览** (验证数据接缝)
- TS: 上传组件 + 概览页 (统计卡 + 1 张图)
- Python: `POST /projects/{id}/corpus` (收文件→转发 R /parse), `GET /projects/{id}/overview` (调 R /overview), Postgres 存 corpus_id + 元数据
- R: `POST /parse` (WoS/Scopus → 语料 parquet, 返 corpus_id), `POST /overview` (载 corpus_id → bibliometrix 概览 + ggplot 图)
- 验证: 文件跨边界上传、R 温热 + 语料持久化、JSON 契约、Postgres、docker-compose dev、部署
- 对照: 概览数字逐项对齐 v0.6

**切片 2 — AI 综述写作(一段)流式输出** (验证 agent/流式接缝)
- Python: agent 编排骨架 + SSE 流式端点 + LLM 客户端 (移植 prompts/grounding/cite-check 逻辑)
- TS: 写作/对话 UI + EventSource 流式渲染 + markdown 安全渲染
- 验证: SSE 流式、LLM 调用、抗幻觉 grounding + 引用校验行为对齐 v0.6

---

## 5. 阶段路线

```
Phase 0  地基/骨架
  · tag v0.6-shiny; 现 Shiny → legacy-shiny/; 建 apps/web + services/{agent,r-analysis} + packages/contracts 骨架
  · docker-compose: web + agent + r-analysis + postgres 一键起
  · packages/contracts: 第一版 OpenAPI (corpus/overview)
  · CI 三栈分流

Phase 1  垂直切片 1 (上传→概览, 打通数据接缝)
  · R: plumber /parse + /overview (移植 fct_analysis 概览 + testthat)
  · Python: corpus 上传/概览端点 + Postgres + httpx 调 R
  · TS: 上传组件 + 概览页 + TanStack Query
  · 对照 v0.6 验证

Phase 2  垂直切片 2 (AI 综述流式, 打通 agent/流式接缝)
  · Python: agent 编排骨架 + SSE 端点 + LLM 客户端 (移植 prompts/grounding)
  · TS: 写作 UI + EventSource + markdown 安全渲染
  · 验证抗幻觉 + 引用校验对齐 v0.6

Phase 3  分析页扇出 (复用 fct_ 层, 可并行)
  · R: 其余 6 端点 + PRISMA (移植 fct_*, 多数近乎直接)
  · Python: 对应端点    · TS: 其余分析页

Phase 4  AI 功能扇出 + 真 agent
  · 移植 v0.6 的 7 个 AI 功能 (筛选/翻译/总结/重写/对话/引用/PDF)
  · 真 agent (多步工具调用: 检索→筛选→分析→写作) + 任务队列

Phase 5  对等 → 切换 → 退役 Shiny
  · 功能对等核对表 (逐项对 v0.6) · 鉴权/部署硬化 · 切换用户 · 退役 legacy-shiny
```

---

## 6. 二阶技术默认值 (推荐, 可改)

| 关注点 | 默认 | 备注 |
|--------|------|------|
| Python 框架 | FastAPI | [Layer 1] |
| agent 编排 | Pydantic AI 或原生 SDK + 轻量循环 | 避免过早上重框架; 多步复杂再考虑 LangGraph |
| 前端状态 | TanStack Query + Zustand | 服务端态 / UI 态分离 |
| 组件库 | shadcn/ui 或 Mantine | 二选一 |
| 数据存储 | Postgres + 对象存储/FS | 关系数据 / 语料 parquet + 产物图 |
| 流式 | SSE (EventSource) | D5 已隐含 |
| 服务间 | REST/JSON + OpenAPI 契约 | 契约在 packages/contracts |
| R 并发 | plumber 多 worker (valve) 或多容器 + 负载 | R 单线程, 靠多进程 |
| 鉴权 | JWT/session | 产品化时定 provider; 沿用用户自带 key 不落盘 |

---

## 7. 测试策略 (跨三栈)

- **R**: testthat — 移植 `fct_` 测试 (大批直接复用) + plumber 端点集成测试
- **Python**: pytest — 单元 (编排/契约/grounding/cite-check) + httpx 调 R 集成测试 (起 R 容器)
- **TS**: vitest (单元/组件) + Playwright (E2E, 已有 .playwright-mcp 经验)
- **跨服务**: docker-compose 起全栈 E2E smoke — 每条垂直切片一条 E2E
- **契约测试**: OpenAPI schema 校验三端不漂移 (多服务最大风险, 必做)
- **LLM eval**: 综述/grounding eval, 以 v0.6 抗幻觉约束为 baseline

---

## 8. NOT in scope (显式延后)

| 项 | 理由 |
|----|------|
| 把 8 个分析"算法"重写成 Python | bibliometrix 留 R, 不重实现 (正确性风险 + 无收益) |
| 多租户/团队协作 | 先单用户产品 |
| Kubernetes/自动扩缩 | docker-compose 起步, 上量再说 |
| 移动端原生 app | 富 web 优先 |
| 实时协作编辑 | 非核心 |
| Python 队列 + R callr worker (替代 plumber) | D4 已选 plumber (交互秒级延迟优先); 此更简方案备查, plumber 运维成本超预期再 spike |
| 切片0 纯接缝验证 | 用户选直接切片1; 若切片1 首测难定位接缝再回插 |

---

## 9. 失败模式 (每条新代码路径)

| # | 失败 | 是否静默 | 需要的处理 |
|---|------|---------|-----------|
| 1 | R plumber worker OOM (大语料 bibliometrix 占内存) | 前端转圈, 半静默 | 健康检查 + 自动重启 + 内存上限 + 50MB 上传限制沿用 |
| 2 | 跨服务超时 (R 分析慢, httpx 超时) | **静默 (502/转圈, 用户不知情)** | **异步任务 + SSE/轮询进度, 非同步阻塞** ← 关键 |
| 3 | 契约漂移 (三端 schema 不一致) | **静默 (字段错位/空白)** | **OpenAPI 契约测试 in CI** ← 关键 |
| 4 | SSE 流中断 (网络断/LLM 超时) | 半静默 (写一半消失) | 断线重连 + 部分结果保存 |
| 5 | 语料持久化竞态 (parse 未完即请求 analysis) | **静默 (读半截 parquet 报错)** | **corpus 状态机 (parsing/ready/failed) + 前端 gating** ← 关键 |
| 6 | R↔Python 大对象传输 (图/大表过 HTTP) | 内存/带宽爆 | 产物按端点定形 (PNG/plotly JSON/nodes-edges DTO), 大表分页 |
| 7 | RDS 反序列化加载不可信文件 = **RCE** (Codex #18) | 静默 (被利用前无感) | **只允许加载系统自己生成的产物**; 用户上传走 parse 不走 readRDS; 语料优先 parquet |

**关键缺口 (无测试 + 无错误处理 + 静默)**: #2 超时、#3 契约漂移、#5 语料竞态 —— 三者必须从第一天就设计错误处理与测试。

---

## 10. 并行化 (worktree 分车道)

```
Lane A  packages/contracts        必须先行 (定义接缝)
Lane B  services/r-analysis       契约冻结后; 可对 mock 推进
Lane C  services/agent            契约冻结后; R 未好前 mock R
Lane D  apps/web                  契约冻结后; agent 未好前 mock agent

执行: 每条切片先冻结契约 (Lane A) → B/C/D 并行 worktree → 切片末集成
扇出期 (Phase 3-4): 每个分析页 = 独立 R 端点 + Python 端点 + TS 页, 可按功能并行
冲突旗标: B/C/D 都依赖 contracts; 契约中途改 → 三车道全churn。缓解: 每切片冻结契约后再扇出。
```

---

## 11. 实现任务 (Phase 0-1 起步, 派生自本设计)

- [ ] **T1 (P1)** — 仓库 — Monorepo 重构: tag v0.6-shiny, Shiny → legacy-shiny/, 建四目录骨架
- [ ] **T2 (P1)** — 基建 — docker-compose 起 web+agent+r-analysis+postgres
- [ ] **T3 (P1)** — 契约 — packages/contracts 定 corpus/overview OpenAPI 第一版
- [ ] **T4 (P1)** — r-analysis — plumber 骨架 + /parse + /overview (移植 fct_analysis 概览 + testthat)
- [ ] **T5 (P1)** — agent — FastAPI 骨架 + corpus/overview 端点 + Postgres + httpx 调 R
- [ ] **T6 (P1)** — web — Vite+React 骨架 + 上传组件 + 概览页 + TanStack Query
- [ ] **T7 (P1)** — 测试 — 切片 1 端到端 E2E (docker-compose) + OpenAPI 契约测试 in CI
- [ ] **T8 (P2)** — agent — SSE 流式端点 + LLM 客户端 (移植 prompts/grounding) [切片 2]
- [ ] **T9 (P2)** — web — 写作 UI + EventSource 流式 + markdown 安全渲染 [切片 2]
- [ ] **T10 (P1, Codex #5 前移)** — r-analysis/agent — corpus 状态机 (parsing/ready/failed + 原子写 + 幂等 job_id + hash/version key) + 超时异步化 [切片1 即需, 不能拖到 P2]
- [ ] **T11 (P1)** — agent — Python 地基: 项目骨架/依赖/配置/auth/Postgres 迁移/结构化日志 (Codex #7: greenfield 成本显性化)
- [ ] **T12 (P2)** — 跨服务 — 观测性: trace id 贯通 web/agent/R + per-job 日志 + LLM token/cost + SSE 断线率 (Codex #14)
- [ ] **T13 (P2)** — agent — 成本治理: 预算/限流/按项目用户统计/重试归因/LLM 审计 (Codex #15)
- [ ] **T14 (P1)** — 安全 — 上传隔离 + 对象存储 ACL + RDS 仅系统产物 + sanitizer TS 等价测试 + 外部 API 限流脱敏 (Codex #18)

---

## 12. Codex 二审修正与补强 (2026-05-21)

> 经 /codex review 独立二审 (gpt-5.5, 只读, 抽查 R/ 实证)。**以下修正取代正文相应乐观表述。**
> 两条交叉张力已由用户拍板。

### 张力裁定
- **① D4 plumber vs 队列+callr**: 维持 plumber (交互秒级延迟优先)。callr 方案入 §8 NOT-in-scope 备查。
- **② 切片粒度**: 不插切片0, 切片1 (上传→概览) 仍为第一条 (用户决定)。

### P0 修正 (采纳)
1. **fct_ 不是一律"近乎原样"** (#1): 纯统计函数→近乎直接; 图/网络函数 (`fct_analysis.R:195/213/227`)→逐端点设计 JSON DTO。"移植逻辑" ≠ "定义跨服务契约"。
2. **语料持久化需语义设计** (#2): 定义 bibliometrixDB class/attrs/字段类型/版本兼容/去重后状态/来源元数据/hash+version key。RDS 保真但绑 R 版本且**反序列化=RCE**; parquet 可查但可能丢 R 语义。倾向 **parquet + 明确 schema**, RDS 仅系统内部产物。
3. **plumber 状态语义先定** (#3): 明确"内存态服务 (语料常驻, 要处理多 worker 缓存一致性/失效/内存倍增)" vs "无状态 (每次磁盘载入, 温热只省包加载)"。建议: **单 worker 内存态 + LRU 上限**起步, 并发不足再横扩。
4. **图产物逐端点定形** (#6): 见 §1 已修。
5. **corpus 状态机前移 Phase 1** (#5): 见 T10。

### P1 补强 (采纳)
6. **Python greenfield 显性化** (#7): 见 T11, 不混进切片任务。
7. **SSE 协议细化** (#8): Last-Event-ID + heartbeat + 断线 replay + chunk 持久化 + 取消 + 重复连接幂等 + 代理超时 + EventSource 鉴权 (查询参/cookie, 非 header)。
8. **key×异步策略** (#9): 长 agent/后台任务**不**用"会话级不落盘 key"; 改服务端 key (成本归项目) 或对长任务加密落盘短 TTL。同步交互仍可用会话 key。边界写进设计。
9. **OpenAPI 治理** (#10): schema-first 生成客户端/服务端 + 响应快照测试 + 错误码枚举 + artifact schema version。仅校验 schema 不防业务语义漂移。
10. **冻结操作细节** (#12): 移 legacy-shiny/ 前**先做可运行容器/启动脚本快照**; 现入口强依赖相对路径 (app.R:3, global.R:35, www/, data/, renv)。
11. **入口覆盖标注** (#13): 切片1 只做 WoS/Scopus, **不得**宣称"数据接缝已验证"。PubMed×3/去重/PRISMA 自动填充/Crossref 修复列入后续, 接缝验证以它们为准。
12. **流式是净新非移植** (#16): 现 DeepSeek 客户端 stream=FALSE (`fct_llm_deepseek.R:49`), `mod_ai_review.R:3` 自注"流式 SSE 推到 Phase2.1 spike 未做"。Phase 2 按净新实现估。

### P2 补强 (采纳)
13. **观测性** (#14): 见 T12。三服务后无此, 调试比单体更差。
14. **成本治理产品级** (#15): 见 T13 (现仅 session 累计 `fct_cost.R:54`)。
15. **对照测试容差** (#17): 网络布局/主题图/LLM 输出/引用校验定义规范化输出 + 容差, 不逐字逐像素。
16. **安全边界** (#18): 见 T14。sanitizer 迁移非"规则照搬"。
