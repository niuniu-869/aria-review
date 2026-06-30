"""A5 · 研究副驾路由 — GAP 发现 / 价值二次验证 / HITL（契约 §2.1 + §2.4）。

5 endpoint（均 HITL：裁决浮现给人，不自动定稿）：
  POST  /projects/{pid}/corpus/{cid}/gaps:discover   → 202 {run_id}   异步 gap 发现
  GET   /projects/{pid}/agent/runs/{rid}/scratchpad   → ScratchpadState（实时 HITL 视图）
  POST  /projects/{pid}/gaps/{gap_id}:verify          → 202 {verify_run_id} 异步价值核验
  GET   /projects/{pid}/gaps/{gap_id}/verdict         → GapVerdictResult（裁决 + 证据包）
  PATCH /projects/{pid}/gaps/{gap_id}                 → GapCandidate（accept/reject/revise）

异步走 AiJob + BackgroundTasks（run_id = str(ai_job.id)，与 gap_candidate.run_id 对齐）。
分层铁律：LLM 攒证 / 确定性 resolver 裁决（decided_by=deterministic）。领域无关（§0.3）。
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request
from pydantic import BaseModel, Field

from .agent.dispatch import OUTCOME_OK
from .agent.registry_factory import build_registry
from .agent.scratchpad import DbScratchpadStore
from .harness.llm import LLMRouter
from .db import SessionLocal, get_session
from .errors import ApiError
from .repositories import ai_job as ai_job_repo
from .repositories import gaps as gaps_repo
from .repositories import project as project_repo
from .review.gap_discover import discover_gaps
from .review.load import has_readable_fulltext, load_project_corpus
from .review.read import summarize_papers
from .review.value_check import ValueCheckError, verify_gap_value

logger = logging.getLogger("agent.routes_research")

router = APIRouter(tags=["research"])

# 路径段白名单（§2.4-4）：禁 `:` `/` 等，避免与 AIP 自定义方法 :discover/:verify 路由歧义。
_SEG = r"^[A-Za-z0-9._-]+$"


# ======================================================================
# 请求模型
# ======================================================================

class GapDiscoverRequest(BaseModel):
    # 契约 §2.1 `gaps:discover` 无请求体（openapi requestBody?: never）；topic 可选，
    # 缺省时由 handler 从项目 research_question/name 派生，使前端「无 body」请求不再 422。
    topic: Optional[str] = None
    lens: Optional[str] = None          # 可选过滤；缺省三 lens 全开
    # codex P2: body 可缺省后, max_candidates 须收口边界 —— 否则 0/负数/过大值被接受,
    # 静默跑出无候选或异常成本。ge=1 le=50 兜住合理区间。
    max_candidates: int = Field(default=12, ge=1, le=50)


class ValueThresholdsIn(BaseModel):
    reverse_hit_high: int = 25
    reverse_hit_low: int = 3


class GapVerifyRequest(BaseModel):
    methods: Optional[list[str]] = None                 # ["reverse_search","biblio_structure"]
    thresholds: Optional[ValueThresholdsIn] = None      # 按领域可调（§0.3）


class GapPatchRequest(BaseModel):
    """HITL 决策（§2.4-3 oneOf）：revise 强制带非空 statement；accept/reject 不带。

    oneOf 一致性在 patch_gap handler 前置校验（先于 gap 存在性），返回可预测的 422。
    """
    human_decision: Literal["accept", "reject", "revise"]
    note: Optional[str] = None
    statement: Optional[str] = None


def _validate_patch_oneof(body: "GapPatchRequest") -> None:
    """§2.4-3 oneOf：revise 必带非空 statement；accept/reject 不应带 statement。fail-loud 422。"""
    if body.human_decision == "revise" and not (body.statement or "").strip():
        raise ApiError(422, "VALIDATION_ERROR", "revise 必须提供非空 statement")
    if body.human_decision != "revise" and body.statement:
        raise ApiError(422, "VALIDATION_ERROR", "accept/reject 不应带 statement")


# ======================================================================
# 序列化 / 状态映射
# ======================================================================

def _run_status(job: Any | None) -> str:
    """AiJob.status → 契约 run_status（前端停轮询信号，§2.4-2）。"""
    st = getattr(job, "status", None)
    if st == "done":
        return "completed"
    if st in ("failed", "error"):
        return "failed"
    return "running"


def _gap_dict(g: Any) -> dict:
    """GapCandidate 域对象 / ORM 行 → 契约 GapCandidate dict。"""
    if hasattr(g, "to_dict"):
        return g.to_dict()
    return {
        "gap_id": g.gap_id, "theme": g.theme, "statement": g.statement, "lens": g.lens,
        "supporting_papers": g.supporting_papers or [], "counter_evidence": g.counter_evidence or [],
        "confidence": g.confidence, "status": g.status, "value_verdict": g.value_verdict,
    }


def _discover_job_update(result: dict) -> dict:
    """从 discover_gaps 返回值决定 job 终态（纯函数，可单测）。返回 {status, error, summary_json, event}。

    铁律（问题3 修复）：discover_gaps 对 subagent 非 ok 是「透出 outcome 不抛异常」
    （gap_discover.py 注释明示「调用方据此置 job 状态」），故必须在此显式检查 outcome——
    非 "ok" 一律置 failed，绝不静默 done。此前调用方无条件 status="done"，把 gap-finder 的
    error（如 read_paper 越界失败耗尽轮次）吞成 run_status=completed + 0 条（静默吞错）。

    completed-empty（codex 二审）：outcome=ok 但 0 条 = 正常跑完未发现，置 done 但标
    summary_json.empty=true + event done_empty，与「系统失败」区分（不改前端状态枚举）。
    """
    outcome = result.get("outcome")
    gaps_n = len(result.get("gaps") or [])
    if outcome != OUTCOME_OK:
        reasons = result.get("tool_failure_reasons") or []
        return {
            "status": "failed",
            "error": (f"gap-finder 未正常完成（outcome={outcome}, "
                      f"tool_failures={result.get('tool_failures')}）：{reasons[:3]}"),
            "summary_json": {"gaps": gaps_n, "outcome": outcome,
                             "tool_failures": result.get("tool_failures")},
            "event": {"type": "error", "outcome": outcome,
                      "tool_failures": result.get("tool_failures")},
        }
    empty = gaps_n == 0
    # codex review P2：done 分支也保留 tool_failures，避免 outcome=ok 但有部分工具失败时
    # 信息被丢弃（不复现主 bug，但保可观测）。
    return {
        "status": "done",
        "error": None,
        "summary_json": {"gaps": gaps_n, "outcome": outcome, "empty": empty,
                         "tool_failures": result.get("tool_failures")},
        "event": {"type": "done_empty" if empty else "done", "gaps": gaps_n,
                  "tool_failures": result.get("tool_failures")},
    }


# ======================================================================
# 背景 worker（自建 session；fail-loud 置 job 状态）
# ======================================================================

async def _run_gap_discover(job_id: int, pid: int, cid: str, topic: str,
                            max_candidates: int, llm: Any, override: Any, r: Any) -> None:
    async with SessionLocal() as s:
        job = await ai_job_repo.get_job(s, pid, job_id)
        if job is None:
            return
        await ai_job_repo.update_job(s, job, status="running", append_event={"type": "started"})
        try:
            markdowns, records, _skipped = await load_project_corpus(s, pid)
            if not markdowns:
                raise ApiError(400, "NO_CORPUS", "项目无可读全文语料（先入库+解析）")
            summaries = await summarize_papers(
                markdowns, topic, concurrency=4, override=override,
            )
            store = DbScratchpadStore(SessionLocal, project_id=pid)
            registry = build_registry(SessionLocal, r)
            result = await discover_gaps(
                topic=topic,
                paper_summaries=[ps.to_dict() for ps in summaries],
                registry=registry, llm_router=llm,
                base_context={"session_factory": SessionLocal, "project_id": pid},
                run_id=str(job_id), store=store, project_id=pid,
                max_candidates=max_candidates, llm_override=override,
            )
            # 问题3 修复：按 outcome 决定终态——非 ok 显式 failed，绝不静默 done。
            upd = _discover_job_update(result)
            await ai_job_repo.update_job(
                s, job, status=upd["status"], complete=(upd["status"] == "done"),
                error=upd["error"],
                summary_json=upd["summary_json"],
                append_event=upd["event"],
            )
        except Exception as e:  # noqa: BLE001 — fail-loud：置 failed，绝不静默
            logger.exception("[gap_discover] run=%s failed", job_id)
            await ai_job_repo.update_job(s, job, status="failed", error=str(e),
                                         append_event={"type": "error", "error": str(e)})


async def _run_gap_verify(job_id: int, pid: int, gap_id: str, thresholds: dict | None,
                          llm: Any, override: Any, r: Any) -> None:
    async with SessionLocal() as s:
        job = await ai_job_repo.get_job(s, pid, job_id)
        if job is None:
            return
        await ai_job_repo.update_job(s, job, status="running", append_event={"type": "started"})
        try:
            rec = await gaps_repo.get_record(s, gap_id)
            if rec is None:
                raise ApiError(404, "GAP_NOT_FOUND", f"GAP {gap_id} 不存在")
            gap = _gap_dict(rec)
            # 计量结构佐证：取 discover run 所属 corpus 的已算共现网络（不重算）。
            graph = None
            disc_job = await ai_job_repo.get_job(s, pid, int(rec.run_id)) if str(rec.run_id).isdigit() else None
            corpus_id = getattr(disc_job, "corpus_id", None)
            if corpus_id:
                st, body = await r.get_conceptual(corpus_id)
                if st == 200 and isinstance(body, dict):
                    graph = body.get("graph")
            registry = build_registry(SessionLocal, r)
            out = await verify_gap_value(
                gap, registry=registry, llm_router=llm,
                base_context={"session_factory": SessionLocal, "project_id": pid},
                graph=graph, thresholds=thresholds, llm_override=override,
            )
            # 回写裁决 + 证据包 + status=verified
            async with SessionLocal() as s2:
                rec2 = await gaps_repo.get_record(s2, gap_id)
                if rec2 is not None:
                    rec2.value_verdict = out["verdict"]
                    rec2.evidence_pack = out["evidence"]
                    if rec2.status == "draft":
                        rec2.status = "verified"
                    await s2.commit()
            await ai_job_repo.update_job(
                s, job, status="done", complete=True,
                summary_json={"verdict": out["verdict"]["verdict"]},
                append_event={"type": "done", "verdict": out["verdict"]["verdict"]},
            )
        except (ValueCheckError, ApiError, Exception) as e:  # noqa: BLE001 — fail-loud
            logger.exception("[gap_verify] run=%s gap=%s failed", job_id, gap_id)
            await ai_job_repo.update_job(s, job, status="failed", error=str(e),
                                         append_event={"type": "error", "error": str(e)})


# ======================================================================
# Endpoints
# ======================================================================

async def _require_project(s: Any, pid: int) -> None:
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")


@router.post("/projects/{pid}/corpus/{cid}/gaps:discover", status_code=202)
async def discover(pid: int, request: Request,
                   background_tasks: BackgroundTasks,
                   body: GapDiscoverRequest | None = None,
                   cid: str = Path(pattern=_SEG),
                   s=Depends(get_session)):
    """启动 GAP 发现 run（用 scratchpad 编排）。返回 run_id 供轮询 scratchpad。

    契约 §2.1 该 endpoint 无请求体；body 可选。topic 缺省时从项目派生
    （research_question > name），避免前端「无 body」请求 422（A/B seam 对齐）。
    """
    from .main import _llm_override  # lazy：避免与 main 循环导入
    proj = await project_repo.get_project(s, pid)
    if proj is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")
    # 问题1 修复：同步预检该项目有无可读全文语料——无则快速失败 400，不浪费一次异步 run + LLM
    # 调用（此前 OpenAlex 元数据项目会异步跑到 load 空才 failed，用户等半天看到模糊失败）。
    if not await has_readable_fulltext(s, pid):
        raise ApiError(400, "NO_CORPUS",
                       "项目无可读全文语料：GAP 发现需精读全文产生逐字溯源，请先上传 PDF 并完成解析")
    max_candidates = body.max_candidates if body else 12
    # codex P2: body.topic 需 strip —— 否则 "   " 这类纯空白 truthy 会绕过派生链, 把空白
    # topic 写进 job/喂给 gap-finder。先归一化, 空白则落到 research_question/name 派生。
    topic = ((body.topic or "").strip() if body else "") \
        or (proj.research_question or "").strip() \
        or (proj.name or "").strip() \
        or "研究主题"
    job = await ai_job_repo.create_job(
        s, project_id=pid, kind="gap_discover", corpus_id=cid,
        request_json={"topic": topic, "max_candidates": max_candidates},
    )
    # dispatch 需 LLMRouter（非 get_llm_client）；from_config 读 env deepseek。
    background_tasks.add_task(
        _run_gap_discover, job.id, pid, cid, topic, max_candidates,
        LLMRouter.from_config(), _llm_override(request), request.app.state.r_client,
    )
    return {"run_id": str(job.id)}


@router.get("/projects/{pid}/agent/runs/{rid}/scratchpad")
async def get_scratchpad(pid: int, rid: str = Path(pattern=_SEG), s=Depends(get_session)):
    """拉取本 run 的实时 scratchpad（GapCandidate 列表 + run_status 停轮询信号）。"""
    await _require_project(s, pid)
    gaps = await gaps_repo.list_gaps_by_run(s, rid)
    job = await ai_job_repo.get_job(s, pid, int(rid)) if rid.isdigit() else None
    entries = [_gap_dict(g) for g in gaps]
    return {
        "run_id": rid,
        "entries": entries,
        "updated_at": getattr(job, "updated_at", None).isoformat() if getattr(job, "updated_at", None) else "",
        "run_status": _run_status(job),
    }


@router.post("/projects/{pid}/gaps/{gap_id}:verify", status_code=202)
async def verify(pid: int, request: Request, background_tasks: BackgroundTasks,
                 gap_id: str = Path(pattern=_SEG),
                 body: GapVerifyRequest | None = None,
                 s=Depends(get_session)):
    """启动该 GAP 的价值二次验证（反向检索证伪 + 计量结构佐证 → 确定性裁决）。"""
    from .main import _llm_override
    await _require_project(s, pid)
    rec = await gaps_repo.get_record(s, gap_id)
    if rec is None:
        raise ApiError(404, "GAP_NOT_FOUND", f"GAP {gap_id} 不存在")
    thresholds = None
    if body and body.thresholds:
        thresholds = {"reverse_hit_high": body.thresholds.reverse_hit_high,
                      "reverse_hit_low": body.thresholds.reverse_hit_low}
    job = await ai_job_repo.create_job(
        s, project_id=pid, kind="gap_verify", corpus_id=getattr(rec, "corpus_id", None),
        request_json={"gap_id": gap_id, "thresholds": thresholds},
    )
    background_tasks.add_task(
        _run_gap_verify, job.id, pid, gap_id, thresholds,
        LLMRouter.from_config(), _llm_override(request), request.app.state.r_client,
    )
    return {"verify_run_id": str(job.id)}


@router.get("/projects/{pid}/gaps/{gap_id}/verdict")
async def get_verdict(pid: int, gap_id: str = Path(pattern=_SEG), s=Depends(get_session)):
    """取价值裁决 + 攒证的证据包（§2.4-1：裁决 + 证据包复合体）。"""
    await _require_project(s, pid)
    rec = await gaps_repo.get_record(s, gap_id)
    if rec is None:
        raise ApiError(404, "GAP_NOT_FOUND", f"GAP {gap_id} 不存在")
    if not rec.value_verdict:
        raise ApiError(409, "GAP_NOT_VERIFIED", f"GAP {gap_id} 尚未核验（先 POST :verify）")
    return {"gap_id": gap_id, "verdict": rec.value_verdict, "evidence": rec.evidence_pack}


@router.patch("/projects/{pid}/gaps/{gap_id}")
async def patch_gap(pid: int, body: GapPatchRequest,
                    gap_id: str = Path(pattern=_SEG), s=Depends(get_session)):
    """HITL：人工 accept / reject / revise，留痕进 run events。"""
    await _require_project(s, pid)
    _validate_patch_oneof(body)   # 前置 422（先于 gap 存在性，可预测）
    rec = await gaps_repo.get_record(s, gap_id)
    if rec is None:
        raise ApiError(404, "GAP_NOT_FOUND", f"GAP {gap_id} 不存在")
    if body.human_decision == "accept":
        rec.status = "accepted"
    elif body.human_decision == "reject":
        rec.status = "rejected"
    else:  # revise
        rec.statement = body.statement
    await s.commit()
    await s.refresh(rec)
    # 留痕：写入 discover run 的事件流（best-effort，不阻断主流程）
    try:
        if str(rec.run_id).isdigit():
            job = await ai_job_repo.get_job(s, pid, int(rec.run_id))
            if job is not None:
                await ai_job_repo.update_job(s, job, append_event={
                    "type": "hitl_decision", "gap_id": gap_id,
                    "decision": body.human_decision, "note": body.note or "",
                })
    except Exception:  # noqa: BLE001
        logger.warning("[patch_gap] 留痕失败 gap=%s（不阻断）", gap_id)
    return _gap_dict(rec)
