"""Task P2-4 — RunLog（可验证运行日志）聚合。

把"一次 agent 运行"的全部可审计来源聚合成一个 JSON-able dict（schema=runlog/v1）：
  - run：基本元信息（含从 messages 抽取的 prompt、从 state 抽取的 model_used，
    旧快照回退 rounds_log 扫描）
  - messages：完整对话（来自 get_state(...).messages，恢复唯一真源；非原始 snapshot dict）
  - rounds_log：逐轮摘要（含每轮 tool_calls，供 manifest 计数自洽复算）
  - events：哈希链事件流（来自 list_events，按 seq 升序，含 prev_hash/event_hash）
  - tool_invocations：写工具幂等审计（ToolInvocation 表，按 id 升序）
  - evidence_refs：经校验/筛选后用于综述的证据快照（run.evidence_refs）
  - fabricated_spans：被判红的伪造引用片段（validation_summary.fabricated_spans）
  - manifest：计数 + chain_head + 全文 content_sha256（防整体篡改 + 可重建校验）

codex P0：manifest.fabricated_count 取自 validation_summary.fabricated_citations，
*不* 从 evidence_refs 计数——红色引用从不进 evidence_refs（只有 green/yellow 入证据）。

content_sha256 对"整个 body + manifest（去掉 content_sha256 自身）"做 canonical 哈希，
故重建同一 run 得到稳定 hash（验证器据此判定整体未被篡改）。
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ToolInvocation
from ..repositories import agent_run as agent_run_repo

RUNLOG_SCHEMA_VERSION = "runlog/v1"


def _content_sha256(obj: dict) -> str:
    """对 dict 做 canonical json 序列化后取 sha256 hex。

    canonical：sort_keys + 紧凑 separators + default=str（处理 datetime 等非 JSON 原生类型），
    保证同一逻辑内容（键序无关）得到稳定指纹。
    """
    body = json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _extract_prompt(messages: list[dict]) -> str:
    """从 messages 取第一条 user 消息的 content 作为 prompt（无则 ""）。"""
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            # 多模态 content（list）→ 拼接其中的 text 段
            if isinstance(content, list):
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                return "".join(parts)
            return str(content or "")
    return ""


def _extract_model_used(rounds_log: list[dict]) -> str:
    """取最后一条带 "model" 键的 rounds_log 条目的 model（无则 ""）。

    仅作回退：当前 engine 不把 model 写进 rounds_log，权威值在 LoopState.model_used。
    """
    for entry in reversed(rounds_log or []):
        if isinstance(entry, dict) and entry.get("model"):
            return str(entry["model"])
    return ""


def _count_tool_calls(rounds_log: list[dict]) -> int:
    """实际工具调用总数 = 各 rounds_log 条目 tool_calls 数组长度之和（F-13）。

    ToolInvocation 表只记写工具幂等审计，不能反映真实工具用量；每轮实际调用数
    在 rounds_log 的 tool_calls 摘要数组里（engine step_once 追加）。
    """
    return sum(
        len(e.get("tool_calls") or [])
        for e in (rounds_log or [])
        if isinstance(e, dict)
    )


async def build_runlog(s: AsyncSession, run_id: int) -> dict:
    """聚合 run 的可验证日志（schema=runlog/v1）。run 不存在时由调用方先行 404。"""
    run = await agent_run_repo.get_run(s, run_id)
    if run is None:
        raise ValueError(f"AgentRun {run_id} not found")

    # messages 来自重建的 LoopState（恢复唯一真源），而非原始 snapshot dict
    state = await agent_run_repo.get_state(s, run_id)
    messages = list(state.messages) if state is not None else []
    # F-08：model_used 权威值在 state.model_used（engine step_once 每轮写入）；
    # rounds_log 扫描仅作回退（旧快照可能缺 model_used）。
    model_used = ""
    if state is not None and state.model_used:
        model_used = state.model_used
    else:
        model_used = _extract_model_used(run.rounds_log or [])

    # 事件流（按 seq 升序）
    raw_events = await agent_run_repo.list_events(s, run_id)
    events = [
        {
            "seq": e.seq,
            "type": e.type,
            "payload": e.payload,
            "ts": e.ts.isoformat() if e.ts is not None else None,
            "prev_hash": e.prev_hash,
            "event_hash": e.event_hash,
        }
        for e in raw_events
    ]

    # 写工具幂等审计（按 id 升序 = 时间顺序）
    rows = (
        await s.execute(
            select(ToolInvocation)
            .where(ToolInvocation.run_id == run_id)
            .order_by(ToolInvocation.id.asc())
        )
    ).scalars().all()
    tool_invocations = [
        {
            "idempotency_key": t.idempotency_key,
            "tool_id": t.tool_id,
            "action": t.action,
            "result": t.result,
            "created_at": t.created_at.isoformat() if t.created_at is not None else None,
        }
        for t in rows
    ]

    evidence_refs = run.evidence_refs or []
    validation_summary = run.validation_summary or {}
    fabricated_spans = validation_summary.get("fabricated_spans", [])
    rounds_log = run.rounds_log or []

    body = {
        "schema_version": RUNLOG_SCHEMA_VERSION,
        "run": {
            "id": run.id,
            "project_id": run.project_id,
            "status": run.status,
            "prompt": _extract_prompt(messages),
            "model_used": model_used,
            "created_at": run.created_at.isoformat() if run.created_at is not None else None,
            "final_output": run.final_output,
        },
        "messages": messages,
        # F-13：随日志携带 rounds_log（每轮 tool_calls 摘要），manifest 的
        # tool_invocation_count 据此可自洽复算（见 runlog_verify.manifest_counts）。
        "rounds_log": rounds_log,
        "events": events,
        "tool_invocations": tool_invocations,
        "evidence_refs": evidence_refs,
        "fabricated_spans": fabricated_spans,
    }

    manifest = {
        "event_count": len(events),
        # F-13：实际工具调用总数（rounds_log tool_calls 之和），
        # 不再等同于 ToolInvocation 写审计行数（那只覆盖写工具）。
        "tool_invocation_count": _count_tool_calls(rounds_log),
        "evidence_count": len(evidence_refs),
        # codex P0：取自 validation_summary，不从 evidence_refs 计数
        "fabricated_count": validation_summary.get("fabricated_citations", 0),
        "chain_head": events[-1]["event_hash"] if events else "",
    }

    # content_sha256 = hash(整个 body + manifest 去掉 content_sha256 自身)，
    # 故重建同一 run 得到稳定 hash。
    manifest["content_sha256"] = _content_sha256({**body, "manifest": dict(manifest)})

    body["manifest"] = manifest
    return body
