#!/usr/bin/env python3
"""进程内驱动真实 agent 搜索环节（真实 AGENT_SYSTEM + 真实 LLM + 真实 SearchTool），
捕获 LLM 实际构造的检索式 + 自筛后保留的候选。不写 DB、不碰线上服务。

用 stub 替换 project.import_search_results 捕获自筛的 candidate_ids；SearchTool 真实执行(调 R :8001)。
读取仓库根目录下本地忽略的 bench_search/queries.json，输出到同目录。
跑法: services/agent/.venv/bin/python agent_search_bench.py [N]   (N=可选限跑前N条, 调试用)
"""
from __future__ import annotations
import asyncio, json, os, sys, time
from pathlib import Path

import httpx

from app.config import settings
from app.r_client import RClient
from app.harness.tools import ToolRegistry, BaseTool, ToolResult
from app.harness.engine import LoopState, step_once
from app.harness.llm import LLMRouter
from app.agent.context import AgentContext
from app.agent.prompts import AGENT_SYSTEM, WRAP_UP
from app.tools.search import SearchTool

HERE = Path(__file__).resolve().parent
QUERIES = HERE.parent.parent / "bench_search" / "queries.json"
PROMPT_VERSION = os.environ.get("BENCH_PROMPT", "v1")  # v1=当前prompt, v2=改进prompt
OUT = HERE.parent.parent / "bench_search" / (
    "agent_results_sciverse_v2.json" if PROMPT_VERSION == "v2" else "agent_results_sciverse.json"
)
FAKE_PID = 9999
MAX_ROUNDS = 5

if not QUERIES.exists():
    raise SystemExit(
        "本地 benchmark 数据缺失: bench_search/queries.json。"
        "该目录被 .gitignore 忽略；请先在本地准备 queries.json。"
    )

# 让 agent 走 Sciverse 管线(直连 Sciverse API, 不经 R)。生产 AGENT_SYSTEM 只描述了 OpenAlex、
# 不知道 provider 参数和 Sciverse 的存在(P0 缺陷)——这里显式补上, 测 Sciverse 管线 + 锚定+自筛。
SCIVERSE_DIRECTIVE = (
    "\n\n【本次检索数据源】使用 Sciverse 文献库(中英文覆盖)。调用 search__topic 时"
    "**provider 参数必须传 'sciverse'**(不要用 openalex)。Sciverse 语义检索对短/裸/多义"
    "检索式更易跨域漂移, 故务必严格执行上面的领域锚定与逐条自筛。"
)

# v2 改进检索质量段（覆盖生产 prompt 的"宽松多检索+整批"指引）。针对 v1 实测失效模式:
# 自筛过宽(留并集)、多检索式有一条漂移仍并入、真歧义词硬搜多域合并、子主题没卡。
V2_OVERRIDE = (
    "\n\n【检索质量 v2 · 单域锚定 + 严格自筛（本段优先级最高，覆盖上面任何与之冲突的检索指引）】\n"
    "1. 锚定单一最可能领域：先为用户主题判定**一个最可能的学术领域**——多数多义词在学术语境"
    "有明显主导含义（如 progressive collapse→结构工程、attention→深度学习、bridge→桥梁工程、"
    "GAN→生成对抗网络、regression→统计回归），应锚定该主导含义检索并导入，**不要因为词面多义就拒绝**。\n"
    "2. 严禁多域合并（v1 主要病根）：**绝不**对同一主题分别检索多个不同学科方向再把结果并入语料。"
    "若你发现自己在为同一主题同时搜『材料/结构/机器学习』等多个方向，立即停手——这正是把无关领域"
    "混进来的根源。要么锚定单一主导领域，要么(见第5条)停下澄清。\n"
    "3. 准确率优先、逐条丢弃：只纳入你『高度确信』切题的候选；某条检索式过半漂移则**整条丢弃**不并入。"
    "保留集是『精炼相关集』，不是『检索结果的并集』。\n"
    "4. 全约束匹配：候选必须匹配用户主题的**全部限定**含子主题/限定词"
    "（如『效率退化』必须真讨论退化机理，只沾领域不沾子主题 = 不纳入）。\n"
    "5. 仅在真正无主导领域时才澄清：只有当你**完全无法**判定单一最可能领域"
    "（2+领域同等可能且无任何上下文线索，如裸『网络/增强/memory/信号/模型』）时，才停止检索、"
    "列出可能方向请用户澄清（kept 留空）；有明显主导含义的主题不要走这条。"
)


class StubImportTool(BaseTool):
    """捕获 LLM 自筛决定导入的 candidate_ids（不写库）。对齐真实 project.import_search_results 签名。"""
    tool_id = "project"
    tool_name = "Project Tool"
    description = "将检索到的文献候选导入项目语料库（按 candidate_ids 自筛导入）。"
    actions = ["import_search_results"]
    tags = ["write"]
    action_schemas = {
        "import_search_results": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "项目 ID（必填）"},
                "candidate_ids": {"type": "array", "items": {"type": "string"},
                                  "description": "仅导入指定候选 ID（自筛后只传相关的）；不传则整批"},
                "limit": {"type": "integer", "default": 100},
                "default_status": {"type": "string", "enum": ["candidate", "included"], "default": "candidate"},
            },
            "required": ["project_id"],
        },
    }

    async def _execute(self, action, params, context=None):
        if action != "import_search_results":
            return self._fail(action, f"不支持的 action: {action}")
        ctx = context if isinstance(context, dict) else {}
        cands = {str(c.get("candidate_id")): c for c in (ctx.get("search_candidates") or [])}
        wanted = [str(x) for x in (params.get("candidate_ids") or []) if str(x).strip()]
        if wanted:
            kept = [(cid, cands.get(cid, {}).get("title", "")) for cid in wanted]
        else:
            # 省略 candidate_ids = 整批照收（记录为 ALL，反映"没自筛"）
            kept = [(cid, c.get("title", "")) for cid, c in cands.items()]
        cap = ctx.get("_capture")
        if cap is not None:
            cap["import_calls"].append({"explicit_ids": bool(wanted), "kept": kept})
        return self._ok(action, data=[{"imported": len(kept)}], source="stub",
                        summary=f"已导入 {len(kept)} 篇文献到项目语料库。")


def fake_project_block() -> str:
    return (
        f"\n\n【当前工作上下文】你正在为**项目 #{FAKE_PID}「检索质量评测」**服务，"
        f"该项目当前已纳入 0 篇文献。\n"
        f"- 用户说\"本项目/当前项目\"一律指项目 #{FAKE_PID}，不要调用 project.list。\n"
        f"- 导入文献时 project_id 传 {FAKE_PID}。\n"
        f"- 本项目语料为 0，需先检索并纳入相关文献。"
    )


async def run_one(q: dict, registry, llm_router, model_names) -> dict:
    capture = {"searches": [], "import_calls": []}

    async def emit(ev: dict):
        if ev.get("type") == "search_results":
            cands = ev.get("candidates") or []
            capture["searches"].append({
                "query": ev.get("query"), "n": len(cands),
                "provider": ev.get("provider", "openalex"),  # sciverse 分支会带 provider
            })

    tool_ctx = {"emit": emit, "_capture": capture, "run_id": None, "session_factory": None}
    ctx = AgentContext(
        registry=registry, llm_router=llm_router, model_names=model_names,
        system_prompt=AGENT_SYSTEM, tool_ids={"search", "project"},
        max_rounds=MAX_ROUNDS, wrap_up_prompt=WRAP_UP, tool_context=tool_ctx,
    )
    user_msg = f"我想做关于「{q['raw']}」的文献综述，请帮我检索相关文献并把真正相关的纳入项目语料库。"
    if PROMPT_VERSION == "prod":
        # 用生产 AGENT_SYSTEM 原文(已含 v2.1+provider路由), 不加实验指令——端到端验证落地的生产 prompt
        sys_prompt = AGENT_SYSTEM + fake_project_block()
    else:
        sys_prompt = AGENT_SYSTEM + fake_project_block() + SCIVERSE_DIRECTIVE
        if PROMPT_VERSION == "v2":
            sys_prompt += V2_OVERRIDE
    state = LoopState(messages=[
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg},
    ])

    t0 = time.time()
    err = None
    deadline = time.time() + 360  # 单条兜底, 超时引擎强制收尾轮
    try:
        guard = 0
        while state.status == "running" and guard < MAX_ROUNDS + 2:
            state = await step_once(state, ctx, emit=emit, confirm_check=None, deadline=deadline)
            guard += 1
    except Exception as e:
        err = str(e)[:300]

    # 汇总保留集（取最后一次 import；若多次，合并去重）
    kept = {}
    for ic in capture["import_calls"]:
        for cid, title in ic["kept"]:
            kept[cid] = title
    explicit = any(ic["explicit_ids"] for ic in capture["import_calls"])
    res = {
        "id": q["id"], "raw": q["raw"], "intent": q["intent"],
        "category": q["category"], "lang": q["lang"],
        "constructed_queries": [s["query"] for s in capture["searches"]],
        "search_candidate_counts": [s["n"] for s in capture["searches"]],
        "providers_used": sorted({s.get("provider", "openalex") for s in capture["searches"]}),
        "n_searches": len(capture["searches"]),
        "self_filtered": explicit,                # True=显式传了candidate_ids(真自筛)
        "kept_count": len(kept),
        "kept_titles": list(kept.values()),
        "rounds": state.round_idx,
        "duration_s": round(time.time() - t0, 1),
        "final_output": (state.final_output or "")[:600],
        "error": err,
    }
    print(f"  [{q['id']:4}] '{q['raw'][:26]:28}' searches={res['n_searches']} "
          f"queries={res['constructed_queries']} kept={res['kept_count']} "
          f"selffilter={explicit} {('ERR:'+err) if err else ''}", flush=True)
    return res


async def main():
    queries = json.loads(QUERIES.read_text())
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.isdigit():
            queries = queries[:int(arg)]
        else:  # 逗号分隔 ID 子集
            ids = {x.strip() for x in arg.split(",") if x.strip()}
            queries = [q for q in queries if q["id"] in ids]
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    async with httpx.AsyncClient(base_url=settings.r_analysis_url, timeout=timeout, limits=limits) as client:
        r_client = RClient(client)
        registry = ToolRegistry()
        registry.register(SearchTool(r_client))
        registry.register(StubImportTool())
        registry.mark_write_tools("project")
        llm_router = LLMRouter.from_config()
        model_names = [getattr(settings, "deepseek_model", None) or "deepseek-chat"]
        CONC = 4  # Sciverse 直连(不经 R), 但 DeepSeek + Sciverse 速率, 取 4 稳妥
        sem = asyncio.Semaphore(CONC)
        print(f"驱动真实 agent 搜索(Sciverse 管线): {len(queries)} 条, model={model_names}, 并发={CONC}", flush=True)

        async def guarded(q):
            async with sem:
                try:
                    return await run_one(q, registry, llm_router, model_names)
                except Exception as e:
                    print(f"  [{q['id']}] FATAL {e}", flush=True)
                    return {"id": q["id"], "raw": q["raw"], "error": f"FATAL {e}"}

        tasks = [asyncio.create_task(guarded(q)) for q in queries]
        results = []
        for fut in asyncio.as_completed(tasks):
            results.append(await fut)
            OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2))  # 增量落盘
    print(f"\n完成。落盘 {OUT}（{len(results)} 条）")


if __name__ == "__main__":
    asyncio.run(main())
