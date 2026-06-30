"""Sciverse 全文综述批量驱动(无 MinerU, 全文块级溯源, 不 compromise)。

每域: meta-search→content(全文)→content_list 切块→run_review(综述+溯源校验)→落盘。
用法: PYTHONPATH=. python scripts/run_sciverse_reviews.py [N每域目标篇数] [域索引,逗号分隔|all]
DeepSeek 并发: 域内 content 并发取全文 + summarize concurrency=8; 域间串行(控负载)。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

from app.sciverse import SciverseClient, sciverse_config, normalize_meta_result
from app.review.orchestrate import run_review
from app.review.templates import get_template

OUT_DIR = Path(__file__).resolve().parents[2] / "reviews_output"
CONTENT_MAX = 50000          # 单篇全文取前 N 字符(review 内部再截 18k)
CONTENT_CONCURRENCY = 4      # 全文并发取数(Sciverse 有限流, 降并发更稳)
MIN_FULLTEXT_CHARS = 1200    # 视为有效全文的最小长度
RATE_RETRY = 5               # 限流退避重试次数


async def _retry(fn, *, what=""):
    """对 Sciverse 限流('频繁'/429/503)指数退避重试; 其它异常上抛。"""
    delay = 2.0
    for attempt in range(RATE_RETRY):
        try:
            return await fn()
        except Exception as e:
            msg = str(e)
            if ("频繁" in msg or "429" in msg or "rate" in msg.lower() or "SCIVERSE_UNAVAILABLE" in msg) \
                    and attempt < RATE_RETRY - 1:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 20.0)
                continue
            raise
    return None

# 20 域(商科+工程+能源+生医+AI, 最大化泛化验证)。前5为已跑基线; template: master/phd。
DOMAINS = [
    # —— 商科/会计金融(基线5的前3) ——
    {"key": "esg_disclosure", "topic": "ESG 信息披露的信息含量与市场反应",
     "query": "ESG disclosure information content market reaction", "template": "master"},
    {"key": "earnings_management", "topic": "盈余管理与盈余质量",
     "query": "earnings management earnings quality accruals real activities", "template": "master"},
    {"key": "digital_transformation", "topic": "企业数字化转型的经济后果",
     "query": "corporate digital transformation economic consequences firm performance", "template": "master"},
    # —— 工程(基线5的后2) ——
    {"key": "crashworthiness", "topic": "结构抗撞击性能研究",
     "query": "structural crashworthiness impact energy absorption thin-walled", "template": "master"},
    {"key": "smart_structures", "topic": "智能结构设计",
     "query": "smart intelligent structures design adaptive shape memory actuator", "template": "master"},
    # —— 商科扩展 ——
    {"key": "audit_quality", "topic": "审计质量与审计师行为",
     "query": "audit quality auditor industry specialization tenure", "template": "master"},
    {"key": "corporate_governance", "topic": "公司治理与高管薪酬",
     "query": "corporate governance executive compensation board structure", "template": "master"},
    {"key": "analyst_forecast", "topic": "分析师预测行为与信息中介",
     "query": "financial analyst forecast accuracy information intermediary", "template": "master"},
    {"key": "green_finance", "topic": "绿色金融与气候风险定价",
     "query": "green finance climate risk pricing carbon emission", "template": "master"},
    {"key": "institutional_investors", "topic": "机构投资者与公司决策",
     "query": "institutional investors monitoring corporate decision ownership", "template": "master"},
    # —— 工程/能源扩展 ——
    {"key": "additive_manufacturing", "topic": "增材制造的工艺与性能",
     "query": "additive manufacturing 3D printing process microstructure mechanical", "template": "master"},
    {"key": "battery_safety", "topic": "锂电池热失控与安全",
     "query": "lithium ion battery thermal runaway safety degradation", "template": "master"},
    {"key": "wind_turbine_fatigue", "topic": "风力发电机叶片疲劳与可靠性",
     "query": "wind turbine blade fatigue reliability composite damage", "template": "master"},
    {"key": "perovskite_solar", "topic": "钙钛矿太阳能电池",
     "query": "perovskite solar cell efficiency stability photovoltaic", "template": "master"},
    {"key": "carbon_capture", "topic": "碳捕集利用与封存",
     "query": "carbon capture utilization storage CO2 adsorption", "template": "master"},
    # —— 计算/AI ——
    {"key": "autonomous_driving", "topic": "自动驾驶环境感知",
     "query": "autonomous driving perception sensor fusion object detection", "template": "master"},
    {"key": "medical_imaging_ai", "topic": "深度学习医学影像诊断",
     "query": "deep learning medical imaging diagnosis segmentation", "template": "master"},
    {"key": "federated_learning", "topic": "联邦学习与隐私保护",
     "query": "federated learning privacy preserving distributed model", "template": "master"},
    # —— 生医/材料 ——
    {"key": "gut_microbiome", "topic": "肠道菌群与代谢疾病",
     "query": "gut microbiome metabolic disease obesity diabetes", "template": "master"},
    {"key": "graphene_energy", "topic": "石墨烯储能材料",
     "query": "graphene energy storage supercapacitor electrode", "template": "master"},
]


def md_to_content_list(md: str) -> list:
    """Sciverse 全文 markdown → MinerU 风格 content_list(供 EvidenceResolver 块级溯源)。"""
    blocks = []
    for para in md.split("\n\n"):
        s = para.strip()
        if not s:
            continue
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            blocks.append({"type": "text", "text": s.lstrip("# ").strip(),
                           "text_level": max(1, level), "page_idx": 0})
        elif s.startswith("![](") and s.endswith(")"):
            blocks.append({"type": "image", "img_path": s, "page_idx": 0})
        else:
            blocks.append({"type": "text", "text": s, "page_idx": 0})
    return blocks


async def _fetch_one(sc: SciverseClient, doc_id: str, sem: asyncio.Semaphore) -> str:
    async with sem:
        try:
            body = await _retry(lambda: sc.content(doc_id, offset=0, limit=CONTENT_MAX),
                                what=f"content({doc_id[:8]})")
            return (body or {}).get("text", "") or ""
        except Exception:
            return ""


async def gather_fulltext_corpus(sc: SciverseClient, query: str, target: int) -> tuple[list, list]:
    """meta-search 分页拿候选 → 并发取全文 → 凑够 target 篇有效全文。返回 (markdowns, records)。"""
    candidates: list[dict] = []
    seen_doc: set[str] = set()
    for page in range(1, 9):                        # 最多翻 8 页(限流退避)
        ms = await _retry(lambda p=page: sc.meta_search(query=query, page_size=50, page=p),
                          what=f"meta-search p{page}")
        rows = (ms or {}).get("results", [])
        if not rows:
            break
        for r in rows:
            p = normalize_meta_result(r)
            doc = p.get("sciverseDocId")
            if doc and doc not in seen_doc:
                seen_doc.add(doc)
                candidates.append(p)
        if len(candidates) >= target * 4:           # 候选足够(按 ~1/4 全文产出率冗余)
            break
        await asyncio.sleep(0.5)                     # 翻页间隔, 缓限流
    sem = asyncio.Semaphore(CONTENT_CONCURRENCY)
    texts = await asyncio.gather(*[_fetch_one(sc, p["sciverseDocId"], sem) for p in candidates])

    mds, recs = [], []
    for p, txt in zip(candidates, texts):
        if len(txt) < MIN_FULLTEXT_CHARS:
            continue
        i = len(mds) + 1
        authors = ";".join(p.get("authors") or [])
        mds.append({"meta": {"paper_id": i, "title": p["title"], "authors": authors,
                             "year": p.get("year")},
                    "markdown": txt, "content_list": md_to_content_list(txt)})
        recs.append({"idx": i, "paper_id": i, "title": p["title"], "authors": authors,
                     "year": p.get("year"), "doi": p.get("doi"),
                     "content_sha256": f"sv_{p['sciverseDocId'][:16]}",
                     "sciverseDocId": p["sciverseDocId"]})
        if len(mds) >= target:
            break
    return mds, recs


async def run_domain(sc: SciverseClient, d: dict, target: int) -> dict:
    t0 = time.monotonic()
    print(f"\n[{d['key']}] meta-search + 全文取数 (target {target})...", flush=True)
    mds, recs = await gather_fulltext_corpus(sc, d["query"], target)
    print(f"[{d['key']}] 有效全文 {len(mds)} 篇 "
          f"(avg {sum(len(m['markdown']) for m in mds)//max(1,len(mds))} 字符)", flush=True)
    if len(mds) < 5:
        return {"key": d["key"], "ok": False, "reason": f"全文不足({len(mds)})", "papers": len(mds)}

    result = await run_review(d["topic"], mds, recs,
                              template=get_template(d["template"]), concurrency=8)
    md = result.get("review_md", "")
    if result.get("error"):
        return {"key": d["key"], "ok": False, "reason": str(result["error"]), "papers": len(mds)}
    vsum = result.get("validation_summary") or {}
    prov = result.get("provenance_map") or {}

    dd = OUT_DIR / d["key"]
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "review.md").write_text(md, encoding="utf-8")
    (dd / "records.json").write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    (dd / "provenance_map.json").write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {"key": d["key"], "topic": d["topic"], "query": d["query"], "ok": True,
            "papers": len(mds), "review_chars": len(md),
            "validation_summary": vsum, "provenance_entries": len(prov),
            "elapsed_s": round(time.monotonic() - t0, 1)}
    (dd / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{d['key']}] ✅ review {len(md)}字 | 引用校验 {vsum.get('valid_citations')}有效"
          f"/{vsum.get('fabricated_citations')}伪造 | 溯源 {len(prov)} | {meta['elapsed_s']}s", flush=True)
    return meta


async def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    sel = sys.argv[2] if len(sys.argv) > 2 else "all"
    domains = DOMAINS if sel == "all" else [DOMAINS[int(i)] for i in sel.split(",")]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = sciverse_config()
    results = []
    async with httpx.AsyncClient(base_url=cfg.base_url, timeout=90) as hc:
        sc = SciverseClient(cfg, hc)
        for d in domains:                          # 域间串行(控负载/避免限流)
            try:
                results.append(await run_domain(sc, d, target))
            except Exception as e:
                print(f"[{d['key']}] ❌ 异常: {e}", flush=True)
                results.append({"key": d["key"], "ok": False, "reason": str(e)})
    (OUT_DIR / "index.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("ok"))
    print(f"\n===== 完成 {ok}/{len(results)} 域 =====")
    for r in results:
        print(f"  {'✅' if r.get('ok') else '❌'} {r['key']}: "
              f"{r.get('papers','?')}篇 {r.get('review_chars','')}字 {r.get('reason','')}")


if __name__ == "__main__":
    asyncio.run(main())
