"""单域 GAP 发现 + 价值二次验证(端到端, 全 sciverse, agentic subagent)。

gather全文→run_review(复用summaries)→discover_gaps(gap-finder subagent)→
r计量网络→verify_gap_value(value-evidence subagent反向检索+确定性resolver)。
用法: PYTHONPATH=. python scripts/run_gap_value.py [域索引] [目标篇数]
"""
import asyncio, json, sys, time
from pathlib import Path
import httpx

from app.sciverse import SciverseClient, sciverse_config, normalize_meta_result
from app.review.orchestrate import run_review
from app.review.templates import get_template
from app.review.gap_discover import discover_gaps
from app.review.value_check import verify_gap_value
from app.agent.scratchpad import InMemoryScratchpadStore
from app.agent.registry_factory import build_registry
from app.harness.llm import LLMRouter
from app.r_client import RClient
from app.db import SessionLocal

sys.path.insert(0, str(Path(__file__).parent))
from run_sciverse_reviews import DOMAINS, gather_fulltext_corpus, OUT_DIR  # noqa: E402


async def main():
    di = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    target = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    d = DOMAINS[di]
    cfg = sciverse_config()
    t0 = time.monotonic()
    async with httpx.AsyncClient(base_url=cfg.base_url, timeout=90) as hc, \
               httpx.AsyncClient(base_url="http://localhost:8001", timeout=180) as rhc:
        sc = SciverseClient(cfg, hc)
        rc = RClient(rhc)
        print(f"[{d['key']}] 取 {target} 篇全文...", flush=True)
        mds, recs = await gather_fulltext_corpus(sc, d["query"], target)
        print(f"  全文 {len(mds)} 篇", flush=True)
        if len(mds) < 5:
            print("全文不足"); return

        # 1) review(复用其 summaries 喂 gap)
        rev = await run_review(d["topic"], mds, recs, template=get_template(d["template"]), concurrency=8)
        summaries = [s for s in (rev.get("summaries") or []) if not s.is_error()]
        print(f"  review {len(rev.get('review_md',''))}字, summaries {len(summaries)}", flush=True)

        # 2) R 计量网络(结构佐证)
        graph = None
        try:
            st, body = await rc.parse_from_records(recs)
            cid = (body or {}).get("corpusId")
            if cid:
                st2, cbody = await rc.get_conceptual(cid)
                if st2 == 200:
                    graph = (cbody or {}).get("graph")
            print(f"  R语料 {cid} 共现图节点 {len((graph or {}).get('nodes',[]))}", flush=True)
        except Exception as e:
            print(f"  R计量网络跳过: {e}", flush=True)

        # 3) GAP 发现(gap-finder subagent)
        llm_router = LLMRouter.from_config()
        registry = build_registry(SessionLocal, rc)
        papers_ctx = {m["meta"]["paper_id"]: {"full_md": m["markdown"],
                      "content_list": m["content_list"], "page_map": {}} for m in mds}
        store = InMemoryScratchpadStore()
        gap_res = await discover_gaps(
            topic=d["topic"], paper_summaries=[s.to_dict() for s in summaries],
            registry=registry, llm_router=llm_router,
            base_context={"papers": papers_ctx, "session_factory": SessionLocal,
                          "sciverse": {"base_url": cfg.base_url, "api_token": cfg.api_token}},
            run_id=f"drv_{d['key']}", store=store, project_id=None, max_candidates=6,
        )
        gaps = gap_res.get("gaps", [])
        print(f"  GAP 发现: {len(gaps)} 条 (outcome={gap_res.get('outcome')})", flush=True)

        # 4) 价值二次验证(value-evidence subagent 反向检索 + 确定性 resolver)
        verdicts = []
        for g in gaps:
            try:
                out = await verify_gap_value(
                    g, registry=registry, llm_router=llm_router,
                    base_context={"papers": papers_ctx, "session_factory": SessionLocal,
                                  "sciverse": {"base_url": cfg.base_url, "api_token": cfg.api_token}},
                    graph=graph)
                verdicts.append(out)
                v = out["verdict"]
                print(f"    · {g['statement'][:40]}… → {v['verdict']} "
                      f"(命中{out['evidence']['reverse_search']['hit_count']}, {v['decided_by']})", flush=True)
            except Exception as e:
                print(f"    · 核验失败: {e}", flush=True)

        dd = OUT_DIR / d["key"]
        dd.mkdir(parents=True, exist_ok=True)
        (dd / "gaps.json").write_text(json.dumps(gaps, ensure_ascii=False, indent=2), encoding="utf-8")
        (dd / "verdicts.json").write_text(json.dumps(verdicts, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{d['key']}] ✅ gaps={len(gaps)} verdicts={len(verdicts)} | {round(time.monotonic()-t0,1)}s", flush=True)

asyncio.run(main())
