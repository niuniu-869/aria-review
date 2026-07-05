"""AgentRun / AgentEvent 仓储: agent run 的创建、状态持久化、事件追加日志。

设计约定（与现有 repositories 风格一致）：
  - 纯函数式，传入 AsyncSession，自管 commit/refresh。
  - seq 单源在 DB（next_seq + append_event），权威事件历史在 agent_event 表，
    SubscribableEventPublisher 的 ring buffer 仅作重连补发优化。
  - save_state 把整个 LoopState 快照(to_json)写入 messages_snapshot 列(恢复唯一真源)，
      并把查询友好的冗余列写回行：status / cursor(=round_idx) / rounds_log /
      final_output / pending_round / evidence_refs / validation_summary。
  - get_state 反向重建 LoopState；向后兼容旧的「messages_snapshot 为纯 list」格式。

并发约定：单个 run 由单一 asyncio.Task 串行驱动（见 run_controller），
next_seq + append_event 在同一逻辑 run 内不会竞态；uq_agent_event_seq 唯一约束
作为兜底保护（万一并发会抛 IntegrityError，由调用方处理，本任务不会触发）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AgentEvent, AgentRun
from ..run_status import normalize_run_status

if TYPE_CHECKING:  # 避免运行期循环依赖；仅类型标注用
    from ..harness.engine import LoopState


async def create_run(
    s: AsyncSession,
    project_id: int,
    plan: str | None = None,
    auto_confirm: bool = False,
) -> AgentRun:
    """创建一条 AgentRun（status=running），返回已持久化对象。"""
    run = AgentRun(
        project_id=project_id,
        plan=plan,
        status="running",
        auto_confirm=auto_confirm,
    )
    s.add(run)
    await s.commit()
    await s.refresh(run)
    return run


async def get_run(s: AsyncSession, run_id: int) -> AgentRun | None:
    """按 id 查 AgentRun；不存在返回 None。"""
    return await s.get(AgentRun, run_id)


async def save_state(s: AsyncSession, run_id: int, state: "LoopState") -> AgentRun:
    """把整个 LoopState 快照写回 AgentRun 行。

    messages_snapshot 现在持有 *完整 LoopState 快照*（state.to_json()），是恢复的
    唯一真源（语义变更：不再只存 messages 列表）。同时把若干查询友好的冗余列写回行，
    供 list/detail 端点免解析快照即可读：

      messages_snapshot  <- state.to_json()（完整快照，恢复唯一真源）
      status             <- state.status
      cursor             <- state.round_idx
      rounds_log         <- state.rounds_log
      final_output       <- state.final_output
      pending_round      <- state.pending_round
      evidence_refs      <- state.evidence_refs（ReviewTool 写入的证据快照，state 单源）
      validation_summary <- state.validation_summary（校验汇总快照）
    """
    run = await s.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"AgentRun {run_id} not found")
    run.messages_snapshot = state.to_json()
    run.status = normalize_run_status(state.status)
    run.cursor = state.round_idx
    run.rounds_log = state.rounds_log
    run.final_output = state.final_output
    run.pending_round = state.pending_round
    run.evidence_refs = state.evidence_refs
    run.validation_summary = state.validation_summary
    await s.commit()
    await s.refresh(run)
    return run


async def get_state(s: AsyncSession, run_id: int) -> "LoopState | None":
    """载入 AgentRun 并重建 LoopState；run 不存在返回 None。

    向后兼容：messages_snapshot 为 dict（新格式 = 完整快照）→ from_json 直接重建；
    为 list（旧格式 = 纯 messages 列表）→ 用该列表作 messages，其余字段从行的冗余列
    / 默认值补全。snapshot 为空（None）→ 用空 messages + 行列值兜底重建。
    """
    from ..harness.engine import LoopState

    run = await s.get(AgentRun, run_id)
    if run is None:
        return None

    snapshot = run.messages_snapshot
    if isinstance(snapshot, dict):
        return LoopState.from_json(snapshot)

    # 旧格式（list）或空：以行列值补全。
    messages = snapshot if isinstance(snapshot, list) else []
    return LoopState(
        messages=messages,
        round_idx=run.cursor or 0,
        rounds_log=run.rounds_log or [],
        status=normalize_run_status(run.status),
        pending_round=run.pending_round,
        final_output=run.final_output,
        all_tool_results=run.evidence_refs or [],
        evidence_refs=run.evidence_refs or [],
        validation_summary=run.validation_summary,
    )


async def next_seq(s: AsyncSession, run_id: int) -> int:
    """返回下一个事件 seq = max(seq where run_id) + 1；无事件时返回 1。"""
    q = select(func.max(AgentEvent.seq)).where(AgentEvent.run_id == run_id)
    current = (await s.execute(q)).scalar()
    return (current or 0) + 1


async def append_event(
    s: AsyncSession,
    run_id: int,
    seq: int,
    type_: str,
    payload: dict,
) -> AgentEvent:
    """追加一条事件（不可变追加日志）。受 uq_agent_event_seq 唯一约束保护。"""
    ev = AgentEvent(run_id=run_id, seq=seq, type=type_, payload=payload)
    s.add(ev)
    await s.commit()
    await s.refresh(ev)
    return ev


def _event_hash(prev_hash, run_id, seq, type_, payload, ts_iso) -> str:
    """对一条事件（含上一条 prev_hash + ts）算 sha256 摘要，构成防篡改链。"""
    body = json.dumps({"p": prev_hash, "r": run_id, "s": seq, "t": type_, "d": payload, "ts": ts_iso},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def append_event_chained(s, run_id, type_, payload, *, max_retries=5):
    """next_seq + 取上一条 event_hash 作 prev_hash + 算本条 event_hash(含 ts) + 落库。
    ts 在 Python 端显式赋值并纳入 hash（防改时间戳不断链）。撞 uq_agent_event_seq 时重试
    （确认回调/重复 start 单进程兜底，非多 worker 序列器）。"""
    for _ in range(max_retries):
        seq = await next_seq(s, run_id)
        prev = (await s.execute(select(AgentEvent.event_hash).where(AgentEvent.run_id == run_id)
                .order_by(AgentEvent.seq.desc()).limit(1))).scalar() or ""
        # agent_event.ts 列为 TIMESTAMP WITHOUT TIME ZONE，故用 naive UTC（存/hash 同值，自洽）。
        ts = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        h = _event_hash(prev, run_id, seq, type_, payload, ts.isoformat())
        ev = AgentEvent(run_id=run_id, seq=seq, type=type_, payload=payload, ts=ts, prev_hash=prev, event_hash=h)
        s.add(ev)
        try:
            await s.commit()
            await s.refresh(ev)
            return ev
        except IntegrityError:
            await s.rollback()
            continue
    raise RuntimeError(f"append_event_chained 重试失败 run={run_id}")


async def list_events(
    s: AsyncSession,
    run_id: int,
    after_seq: int = 0,
) -> list[AgentEvent]:
    """返回 run 的事件流（seq > after_seq，按 seq 升序）——权威历史。"""
    q = (
        select(AgentEvent)
        .where(AgentEvent.run_id == run_id, AgentEvent.seq > after_seq)
        .order_by(AgentEvent.seq.asc())
    )
    return list((await s.execute(q)).scalars().all())


async def list_runs(s: AsyncSession, project_id: int) -> list[AgentRun]:
    """列出某 project 的所有 AgentRun（按 created_at 倒序）。"""
    q = (
        select(AgentRun)
        .where(AgentRun.project_id == project_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
    )
    return list((await s.execute(q)).scalars().all())


def _extract_user_prompt(snapshot) -> str:
    """从一条 run 的快照里取「本 run 的原始用户指令」。

    权威来源是快照顶层的 "user_prompt"（create 时 seed，LoopState 持久化）——**必须**用它，
    不能从 messages 里猜：注入多轮历史后 messages[1] 是历史 user 而非本 run 的 user，扫
    「第一条 role=user」会错抽成最早的历史指令，导致第三轮起历史 user/assistant 错配
    （codex P1）。

    回退：旧快照（本次改动前落库）无 "user_prompt" 键——那些 run 当年无注入历史，
    messages[1] 就是真实指令，故退回扫第一条 role=user。兼容新格式（dict 快照）与
    旧格式（纯 list）。取不到返回空串。
    """
    if isinstance(snapshot, dict):
        prompt = snapshot.get("user_prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
        messages = snapshot.get("messages") or []
    elif isinstance(snapshot, list):
        messages = snapshot
    else:
        return ""
    # 回退：旧快照无 user_prompt（无注入历史）→ 第一条 role=user 即原始指令。
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content.strip()
    return ""


async def list_recent_dialog(
    s: AsyncSession,
    project_id: int,
    *,
    exclude_run_id: int | None = None,
    max_turns: int = 6,
) -> list[tuple[str, str]]:
    """返回本项目最近若干轮「已完成对话」的 (用户指令, 最终回复) 列表，**时间正序**。

    供 RunController.create 拼进新 run 的初始 messages，实现 agent 工作台跨消息的真实
    多轮对话记忆（每条消息本是一条独立 run，初始只 seed 当前 prompt，遂丢上文）。

    取舍：
      - 仅取 status=done 且 final_output 非空的 run —— 半途 failed/cancelled/进行中的
        run 没有可复述的完整回合，跳过（避免把残缺上下文喂回模型）。
      - user 指令由 _extract_user_prompt 权威取回（快照顶层 user_prompt）；取不到跳过该轮。
      - 多取 max_turns*3 行再过滤，最后截断到 max_turns 并反转为时间正序。
      - exclude_run_id 排除刚建的当前 run（其 status 仍是 running，本已被 status 过滤，
        此参数为双保险）。
    """
    q = (
        select(AgentRun)
        .where(
            AgentRun.project_id == project_id,
            AgentRun.status == "done",
            AgentRun.final_output.isnot(None),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(max_turns * 3)
    )
    rows = list((await s.execute(q)).scalars().all())
    turns: list[tuple[str, str]] = []
    for run in rows:
        if exclude_run_id is not None and run.id == exclude_run_id:
            continue
        reply = (run.final_output or "").strip()
        if not reply:
            continue
        prompt = _extract_user_prompt(run.messages_snapshot)
        if not prompt:
            continue
        turns.append((prompt, reply))
        if len(turns) >= max_turns:
            break
    turns.reverse()  # DB 取的是倒序，反转成时间正序（旧 → 新）
    return turns
