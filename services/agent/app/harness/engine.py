"""Agent 执行引擎核心 — Autonomous Function Calling 循环

提供通用的多轮 function calling 引擎，支持：
- 自主决策循环（LLM 决定何时调用工具、何时停止）
- 三阶段上下文窗口裁剪（soft → medium → hard）
- 中途研究备忘录（压缩累积工具结果）
- 读/写工具智能并发策略

移植自 QuantHatch agent_engine，删除 Redis Pub/Sub 依赖（改用内存发布器）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.agent.context import AgentContext

from .config import get_config
from .events import EventPublisher, EventType, NullEventPublisher, publish_run_event
from .llm import LLMRouter, OverrideLLMConfig, call_llm_with_fallback, _sanitize_error
from .tools import BaseTool, ToolRegistry, ToolResult

logger = logging.getLogger("agent_engine.engine")


# ======================================================================
# Token 估算
# ======================================================================

def estimate_str_tokens(text: str) -> int:
    """按字符类型分别估算 token 数

    CJK 字符: 1 token ≈ 1.5 字符
    ASCII 字符: 1 token ≈ 4 字符
    """
    cjk = 0
    ascii_count = 0
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
                or 0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x2FA1F):
            cjk += 1
        else:
            ascii_count += 1
    return int(cjk / 1.5) + int(ascii_count / 4) + 1


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算消息列表的 token 数"""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        # 兼容多模态 content（list[dict] 格式）
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += estimate_str_tokens(part.get("text", ""))
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    total += 258  # 图片按 258 token 估算
            total += 4
        else:
            total += estimate_str_tokens(content) + 4
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            args = func.get("arguments", "")
            total += estimate_str_tokens(args) + 4
    return total


# ======================================================================
# 上下文窗口管理 — 三阶段裁剪
# ======================================================================

def _infer_importance_from_msg(
    messages: list[dict],
    tool_msg_idx: int,
    importance_scores: dict[str, int],
) -> int:
    """从 tool 消息反查 assistant 中的函数名，推断重要性"""
    call_id = messages[tool_msg_idx].get("tool_call_id", "")
    if not call_id:
        return 5
    for j in range(tool_msg_idx - 1, -1, -1):
        msg = messages[j]
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            if tc.get("id") == call_id:
                func_name = tc.get("function", {}).get("name", "")
                tool_id = func_name.split("__", 1)[0] if "__" in func_name else func_name
                return importance_scores.get(tool_id, 5)
        break
    return 5


def trim_messages_to_fit(
    messages: list[dict],
    budget: int,
    importance_scores: dict[str, int] | None = None,
) -> list[dict]:
    """三阶段裁剪消息列表使其不超过 token 预算

    Phase 1 (soft): 按工具重要性截断旧工具结果
    Phase 2 (medium): 非最近 6 条的工具结果替换为单行摘要
    Phase 3 (hard): 删除最早的完整轮次，保留 system + user + 最近 4 轮

    Args:
        messages: 消息列表
        budget: token 预算
        importance_scores: 工具重要性评分 {tool_base_name: score(0-9)}，None 则全部默认 5
    """
    importance_scores = importance_scores or {}

    if estimate_messages_tokens(messages) <= budget:
        return messages

    # 找出所有 tool 消息索引（跳过 system/user）
    tool_idx = [i for i, m in enumerate(messages) if i >= 2 and m.get("role") == "tool"]
    if not tool_idx:
        return messages

    # ---- Phase 1: 按重要性截断旧 tool 结果 ----
    keep_recent = min(6, len(tool_idx))
    old_idx = tool_idx[:-keep_recent] if keep_recent < len(tool_idx) else []

    for i in old_idx:
        c = messages[i].get("content", "")
        importance = _infer_importance_from_msg(messages, i, importance_scores)
        if importance >= 8:
            max_len = 2000
        elif importance >= 6:
            max_len = 600
        else:
            max_len = 300
        if len(c) > max_len:
            messages[i] = {**messages[i], "content": c[:max_len] + "\n...[truncated]"}

    if estimate_messages_tokens(messages) <= budget:
        return messages

    # ---- Phase 2: 非最近 6 条替换为单行摘要 ----
    for i in old_idx:
        c = messages[i].get("content", "")
        if len(c) > 80:
            first_line = c.split("\n", 1)[0][:80]
            messages[i] = {**messages[i], "content": f"[summary] {first_line}"}

    if estimate_messages_tokens(messages) <= budget:
        return messages

    # ---- Phase 3: 按完整 assistant 轮删除（codex P1）----
    # 最小删除单元 = 「一个带 tool_calls 的 assistant 消息 + 它的全部 tool 响应消息」。
    # 从最早的轮开始整轮删，直到满足预算；保证产出永不出现「assistant.tool_calls 缺
    # 对应 tool」或「孤立 tool」。删完做双向孤立清理兜底（应对历史遗留的不配对消息）。
    remove: set[int] = set()

    # 1) 枚举「完整轮」：每个带 tool_calls 的 assistant（idx）→ 它名下全部 tool 响应 idx。
    #    tool 响应按 call_id 归属对应 assistant（在 assistant 之后、紧邻的 tool 消息）。
    rounds: list[tuple[int, list[int]]] = []  # [(assistant_idx, [tool_idx, ...]), ...]
    for i, m in enumerate(messages):
        if i < 2:  # system + user 永不删
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            call_ids = {tc.get("id", "") for tc in m["tool_calls"]}
            tool_idxs: list[int] = []
            for k in range(i + 1, len(messages)):
                mk = messages[k]
                if mk.get("role") == "tool" and mk.get("tool_call_id", "") in call_ids:
                    tool_idxs.append(k)
                elif mk.get("role") == "assistant" and mk.get("tool_calls"):
                    break  # 下一轮 assistant 起，停止收集本轮 tool
            rounds.append((i, tool_idxs))

    # 2) 从最早的完整轮开始整轮删，直到满足预算（保留最近的轮）。
    for a_idx, t_idxs in rounds:
        if estimate_messages_tokens(
            [m for j, m in enumerate(messages) if j not in remove]
        ) <= budget:
            break
        remove.add(a_idx)
        remove.update(t_idxs)

    # 3) 双向孤立清理（兜底）：删轮后若仍残留不配对消息（如历史遗留），一并清掉，
    #    保证产出是合法 OpenAI 序列。
    if remove:
        changed = True
        while changed:
            changed = False
            kept_tool_call_ids = {
                m.get("tool_call_id") for j, m in enumerate(messages)
                if j not in remove and m.get("role") == "tool"
            }
            kept_assistant_call_ids: set[str] = set()
            for j, m in enumerate(messages):
                if j in remove:
                    continue
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        kept_assistant_call_ids.add(tc.get("id", ""))

            for j, m in enumerate(messages):
                if j < 2 or j in remove:
                    continue
                # ① 带 tool_calls 的 assistant：OpenAI 序列要求其**每一个** tool_call_id
                #    都有对应 tool 响应（codex P1）。只要有任一缺失就删整条 assistant——
                #    旧逻辑 `not ids & kept`（交集为空才删）会保留「部分有响应」的 assistant，
                #    产出缺响应的非法序列。删后其已保留的那部分 tool 响应会在 ② 变孤立，
                #    由下一轮 while（changed=True）一并清掉，保证最终合法。
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    ids = {tc.get("id") for tc in m["tool_calls"]}
                    if not ids <= kept_tool_call_ids:
                        remove.add(j)
                        changed = True
                # ② 孤立 tool（无对应 assistant 声明）→ 删。
                if m.get("role") == "tool":
                    cid = m.get("tool_call_id", "")
                    if cid and cid not in kept_assistant_call_ids:
                        remove.add(j)
                        changed = True

        messages = [m for j, m in enumerate(messages) if j not in remove]
        logger.info(f"[ContextTrim] Phase 3: removed {len(remove)} old messages")

    return messages


def build_research_memo(messages: list[dict], start_idx: int) -> str:
    """从 start_idx 开始提取工具结果摘要作为研究备忘录"""
    findings: list[str] = []
    for i in range(start_idx, len(messages)):
        msg = messages[i]
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if not content or content.startswith("[summary]"):
            continue
        first_line = content.split("\n", 1)[0][:120]
        if first_line:
            findings.append(f"- {first_line}")
    if not findings:
        return ""
    return "## Research Memo (auto-generated)\n" + "\n".join(findings[:15])


def _truncate_tool_content(text: str, limit: int = 4000) -> str:
    """截断过长的工具结果，按行边界截断"""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut] + f"\n...[truncated, original {len(text)} chars]"


# ======================================================================
# 工具执行
# ======================================================================

async def execute_tool_calls(
    registry: ToolRegistry,
    tool_calls: list[dict],
    context: Any = None,
    concurrency: int = 8,
    tool_timeout: int = 60,
    tool_result_max_chars: int = 4000,
    extra_params: dict[str, Any] | None = None,
) -> list[dict]:
    """执行一批 tool_calls，返回 tool role 消息列表

    智能并发策略：
    - 写工具（registry 标记的）：串行执行
    - 读工具：并发执行（受 concurrency 限制）

    Args:
        registry: 工具注册中心
        tool_calls: LLM 返回的 tool_calls 列表
        context: 透传给工具的执行上下文
        concurrency: 并发上限
        tool_timeout: 单个工具超时秒数
        tool_result_max_chars: 工具结果最大字符数
        extra_params: 注入到每个工具调用的额外参数
    """
    sem = asyncio.Semaphore(concurrency)

    async def _run_one(tc: dict) -> dict:
        call_id = tc.get("id", "")
        func = tc.get("function", {})
        full_name = func.get("name", "")
        raw_args = func.get("arguments", "{}")

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            logger.warning(f"[ToolCall] JSON parse failed: {raw_args[:200]}")
            # 产出 failed ToolResult（含 _tool_result）而非裸 content：否则该失败不进
            # all_tool_results，下游(如 subagent dispatch 失败统计)会把"参数解析失败"误当
            # "成功无结果"，违背 fail-loud（codex A3 二审 P2）。
            if "__" in full_name:
                _tid, _act = full_name.rsplit("__", 1)
            else:
                _tid, _act = (full_name or "unknown"), "default"
            err_result = ToolResult(
                tool_id=_tid, action=_act, success=False,
                error=f"Argument parse error, check JSON format: {raw_args[:200]}",
            )
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": _truncate_tool_content(err_result.to_prompt_text(), tool_result_max_chars),
                "_tool_result": err_result,
            }

        # 注入额外参数
        if extra_params:
            args.update(extra_params)

        # 解析 tool_id__action
        if "__" in full_name:
            tool_id, action = full_name.rsplit("__", 1)
        else:
            tool_id = full_name
            tool = registry.get(tool_id)
            action = tool.actions[0] if tool and tool.actions else "default"

        async with sem:
            try:
                result = await asyncio.wait_for(
                    registry.execute(tool_id, action, args, context),
                    timeout=tool_timeout,
                )
            except asyncio.TimeoutError:
                result = ToolResult(
                    tool_id=tool_id, action=action,
                    success=False, error=f"Tool timeout (>{tool_timeout}s)",
                )
            except Exception as e:
                result = ToolResult(
                    tool_id=tool_id, action=action,
                    success=False, error=str(e),
                )

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": _truncate_tool_content(result.to_prompt_text(), tool_result_max_chars),
            "_tool_result": result,
        }

    results: list[dict] = []
    pending_parallel: list[dict] = []

    async def _drain_parallel() -> None:
        nonlocal pending_parallel, results
        if not pending_parallel:
            return
        group = pending_parallel
        pending_parallel = []
        group_results = await asyncio.gather(*[_run_one(tc) for tc in group])
        results.extend(group_results)

    def _tool_id_from_tc(tc: dict) -> str:
        fn_name = tc.get("function", {}).get("name", "")
        return fn_name.split("__", 1)[0] if "__" in fn_name else fn_name

    for tc in tool_calls:
        tool_id = _tool_id_from_tc(tc)
        if registry.is_write_tool(tool_id):
            await _drain_parallel()
            results.append(await _run_one(tc))
        else:
            pending_parallel.append(tc)

    await _drain_parallel()
    return results


def _parse_tool_call(tc: dict) -> tuple[str, str, str, dict]:
    """从一条 tool_call 解析出 (tool_call_id, tool_id, action, args)。

    与 execute_tool_calls._run_one 的解析口径一致：name 形如 tool_id__action；
    无 "__" 时 action 取该工具 actions[0]（由调用方在 registry 上下文外退化处理）。
    args 解析失败时返回 {}（具体错误由实际执行路径再行报告）。
    """
    call_id = tc.get("id", "")
    func = tc.get("function", {})
    full_name = func.get("name", "")
    raw_args = func.get("arguments", "{}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except json.JSONDecodeError:
        args = {}
    if "__" in full_name:
        tool_id, action = full_name.rsplit("__", 1)
    else:
        tool_id, action = full_name, ""
    return call_id, tool_id, action, args


async def _execute_single_call(
    registry: ToolRegistry,
    tc: dict,
    *,
    context: Any,
    tool_timeout: int,
    tool_result_max_chars: int,
    extra_params: dict[str, Any] | None,
) -> dict:
    """执行单个 tool_call，返回带 _tool_result 的 tool 消息（确认路径逐个执行用）。

    复用 execute_tool_calls 的单个执行语义（同一批走一条调用即可），保证
    与无确认路径行为一致。"""
    msgs = await execute_tool_calls(
        registry=registry,
        tool_calls=[tc],
        context=context,
        concurrency=1,
        tool_timeout=tool_timeout,
        tool_result_max_chars=tool_result_max_chars,
        extra_params=extra_params,
    )
    return msgs[0]


async def _maybe_idempotent_execute(
    state: "LoopState",
    ctx: AgentContext,
    config: Any,
    tc: dict,
    tool_id: str,
    action: str,
    args: dict,
) -> dict:
    """对写工具做"执行前查 ToolInvocation → 命中跳过、未命中执行后记"的幂等短路。

    仅当 ctx.session_factory + ctx.run_id 同时存在时启用幂等审计；否则直接执行
    （M1 行为）。命中已记录的 invocation → 用其 result 重建 ToolResult，不再二次副作用。
    """
    registry: ToolRegistry = ctx.registry  # type: ignore[assignment]

    async def _run() -> dict:
        return await _execute_single_call(
            registry, tc,
            context=ctx.tool_context,
            tool_timeout=config.tool_timeout,
            tool_result_max_chars=config.tool_result_max_chars,
            extra_params=ctx.extra_tool_params,
        )

    # 仅写工具 + 有 run_id/session_factory 才走幂等审计
    if not (registry.is_write_tool(tool_id) and ctx.session_factory and ctx.run_id):
        return await _run()

    from app.agent.confirm import get_invocation, make_idempotency_key, record_invocation

    key = make_idempotency_key(ctx.run_id, state.round_idx, tool_id, action, args)
    async with ctx.session_factory() as s:
        cached = await get_invocation(s, ctx.run_id, key)
    if cached is not None:
        tr = ToolResult.from_dict(cached)
        return {
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": _truncate_tool_content(tr.to_prompt_text(), config.tool_result_max_chars),
            "_tool_result": tr,
        }

    msg = await _run()
    tr: ToolResult | None = msg.get("_tool_result")
    if tr is not None:
        async with ctx.session_factory() as s:
            await record_invocation(s, ctx.run_id, key, tool_id, action, tr.to_dict())
    return msg


async def _resolve_round_with_confirm(
    state: "LoopState",
    ctx: AgentContext,
    config: Any,
    message: dict,
    tool_calls: list[dict],
    *,
    confirm_check: Callable[[dict], bool],
    emit: Callable[[dict], Awaitable[None]],
) -> tuple[bool, list[dict]]:
    """按原始顺序逐个解决一轮 tool_calls，支持遇写工具确认即挂起。

    Returns:
        (suspended, completed_tool_msgs)
        - suspended=True：已设置 state.status=awaiting_confirmation + state.pending_round，
          并发出 tool_confirm_required；调用方应立即 return state。
        - suspended=False：completed_tool_msgs 为齐备的 tool 消息（带 _tool_result）。
    """
    from app.agent.confirm import make_idempotency_key

    registry: ToolRegistry = ctx.registry  # type: ignore[assignment]
    completed: list[dict] = []

    for pos, tc in enumerate(tool_calls):
        call_id, tool_id, action, args = _parse_tool_call(tc)

        is_write = registry.is_write_tool(tool_id)
        wants_confirm = is_write and bool(confirm_check({
            "tool_id": tool_id, "action": action,
            "tool_call_id": call_id, "args": args,
        }))

        if wants_confirm:
            # —— 挂起：剩余调用（含本写工具）按原顺序入队 ——
            remaining = tool_calls[pos:]
            queue: list[dict] = []
            for rtc in remaining:
                rcid, rtid, raction, rargs = _parse_tool_call(rtc)
                r_is_write = registry.is_write_tool(rtid)
                r_needs = r_is_write and bool(confirm_check({
                    "tool_id": rtid, "action": raction,
                    "tool_call_id": rcid, "args": rargs,
                }))
                queue.append({
                    "tool_call_id": rcid,
                    "tool_id": rtid,
                    "action": raction,
                    "args": rargs,
                    "idempotency_key": (
                        make_idempotency_key(ctx.run_id, state.round_idx, rtid, raction, rargs)
                        if ctx.run_id is not None else None
                    ),
                    "needs_confirm": r_needs,
                })
            # completed_tool_msgs 存 clean（去 _tool_result，保证 JSON-able 可落库）
            clean_completed = [
                {k: v for k, v in m.items() if k != "_tool_result"} for m in completed
            ]
            state.pending_round = {
                "assistant_message": message,
                "completed_tool_msgs": clean_completed,
                "queue": queue,
            }
            state.status = "awaiting_confirmation"
            await emit({
                "type": "tool_confirm_required",
                "toolCallId": call_id,
                "toolId": tool_id,
                "action": action,
                "argsPreview": tc.get("function", {}).get("arguments", "")[:200],
            })
            return True, completed

        # 不需确认 → 立即执行（写工具走幂等短路；读工具直接执行）
        msg = await _maybe_idempotent_execute(
            state, ctx, config, tc, tool_id, action, args,
        )
        completed.append(msg)

    return False, completed


def _queue_item_to_tc(item: dict) -> dict:
    """把 pending_round.queue 里的一项还原成 LLM tool_call 形态（供执行路径复用）。

    queue 项是 _resolve_round_with_confirm 落库的 clean dict（tool_call_id/tool_id/
    action/args/...）。还原成 {"id":..., "function":{"name": tool_id__action, "arguments": json}}
    以喂给 _execute_single_call / _maybe_idempotent_execute（与无确认路径解析口径一致）。
    """
    tool_id = item.get("tool_id", "")
    action = item.get("action", "")
    name = f"{tool_id}__{action}" if action else tool_id
    return {
        "id": item.get("tool_call_id", ""),
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(item.get("args", {}), ensure_ascii=False),
        },
    }


async def _execute_queue_item(
    state: "LoopState",
    ctx: AgentContext,
    config: Any,
    item: dict,
) -> dict:
    """执行 pending_round.queue 的一项，返回 clean tool 消息（去 _tool_result）。

    写工具走幂等短路：优先用 item 里预算好的 idempotency_key 查 ToolInvocation；命中
    复用结果跳过执行，未命中执行后记录。读工具直接执行。与 step_once 的执行语义一致，
    保证 approve resume 后整轮 tool 响应可序列化落进 state.messages。
    """
    from app.agent.confirm import get_invocation, record_invocation

    registry: ToolRegistry = ctx.registry  # type: ignore[assignment]
    tool_id = item.get("tool_id", "")
    action = item.get("action", "")
    tc = _queue_item_to_tc(item)
    key = item.get("idempotency_key")

    is_write = registry.is_write_tool(tool_id)
    can_audit = bool(is_write and key and ctx.session_factory and ctx.run_id)

    if can_audit:
        async with ctx.session_factory() as s:
            cached = await get_invocation(s, ctx.run_id, key)
        if cached is not None:
            tr = ToolResult.from_dict(cached)
            return {
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": _truncate_tool_content(
                    tr.to_prompt_text(), config.tool_result_max_chars,
                ),
            }

    msg = await _execute_single_call(
        registry, tc,
        context=ctx.tool_context,
        tool_timeout=config.tool_timeout,
        tool_result_max_chars=config.tool_result_max_chars,
        extra_params=ctx.extra_tool_params,
    )
    tr: ToolResult | None = msg.get("_tool_result")
    if can_audit and tr is not None:
        async with ctx.session_factory() as s:
            await record_invocation(s, ctx.run_id, key, tool_id, action, tr.to_dict())
    return {k: v for k, v in msg.items() if k != "_tool_result"}


async def apply_confirmation(
    state: "LoopState",
    ctx: AgentContext,
    tool_call_id: str,
    decision: str,
    *,
    emit: Callable[[dict], Awaitable[None]],
) -> "LoopState":
    """对一个 awaiting_confirmation 的 run 处理队首确认决定，按序消费队列。

    前置：调用方已校验 state.status == "awaiting_confirmation"、state.pending_round 非空、
    tool_call_id == queue[0]["tool_call_id"]（顺序校验在 RunController.confirm 做）。

    流程（spec P2-2 F 步骤 4-6）：
      4) 处理队首：
         - approve：队首为写工具 → 幂等执行（命中 ToolInvocation 跳过）→ 追加 tool 消息。
         - reject：追加「用户拒绝执行」tool 消息，不执行。
         弹出队首。
      5) 继续按序消费队列：读工具立即执行追加；遇下一个 needs_confirm 写工具 → 停，
         保持 awaiting_confirmation、发 tool_confirm_required、返回（state 由调用方 save）。
      6) 队列清空：把 assistant_message + 全部 completed_tool_msgs 追加进 state.messages
         （协议完成），清 pending_round，status 置 running，round_idx += 1。

    Returns:
        修改后的 state（in-place）。status ∈ {awaiting_confirmation, running}。
        调用方据 status 决定是否继续驱动（running → resume）。
    """
    config = get_config()
    pr = state.pending_round or {}
    queue: list[dict] = list(pr.get("queue", []))
    completed: list[dict] = list(pr.get("completed_tool_msgs", []))

    # ---- 步骤 4：处理队首 ----
    head = queue.pop(0)
    if decision == "approve":
        msg = await _execute_queue_item(state, ctx, config, head)
        completed.append(msg)
    else:  # reject
        completed.append({
            "role": "tool",
            "tool_call_id": head.get("tool_call_id", ""),
            "content": "用户拒绝执行",
        })

    # ---- 步骤 5：按序消费剩余队列 ----
    while queue:
        nxt = queue[0]
        if nxt.get("needs_confirm"):
            # 下一个写工具仍需确认 → 暂停在此（保持其为队首），等待下一次 confirm。
            state.pending_round = {
                "assistant_message": pr.get("assistant_message"),
                "completed_tool_msgs": completed,
                "queue": queue,
            }
            state.status = "awaiting_confirmation"
            await emit({
                "type": EventType.TOOL_CONFIRM_REQUIRED,
                "toolCallId": nxt.get("tool_call_id", ""),
                "toolId": nxt.get("tool_id", ""),
                "action": nxt.get("action", ""),
                "argsPreview": json.dumps(
                    nxt.get("args", {}), ensure_ascii=False,
                )[:200],
            })
            return state
        # 读工具（或已 auto 的写工具）→ 立即执行、追加。
        item = queue.pop(0)
        msg = await _execute_queue_item(state, ctx, config, item)
        completed.append(msg)

    # ---- 步骤 6：队列清空 → 整轮协议完成 ----
    # completed 项均为 clean dict（无 _tool_result）；state.messages 是恢复唯一真源，
    # 此处把 assistant + 全部 tool 响应一次性落库以补齐被挂起的那一轮。
    assistant_message = pr.get("assistant_message")
    if assistant_message is not None:
        state.messages.append(assistant_message)
    state.messages.extend(completed)
    state.pending_round = None
    state.status = "running"
    state.round_idx += 1
    return state


# ======================================================================
# 可序列化循环状态 + 单步推进
# ======================================================================

@dataclass
class LoopState:
    """autonomous_loop 的可序列化运行期状态。

    把"一次 agent 运行"中所有会变化的东西收进一个可 JSON 化的 dataclass，
    使循环可以暂停 / 持久化 / 恢复（M1 地基；M2 在此之上做人工确认/分支）。

    Attributes:
        messages: 完整对话（含 assistant.tool_calls + tool 消息，均为 clean dict，
                  不含 _tool_result 等运行期对象，保证可 JSON 序列化）
        round_idx: 当前轮序号（从 0 起），== ctx.max_rounds 时进入最终收尾轮
        tool_rounds: 已执行的"有工具调用"的轮数（驱动 memo 插入节奏）
        last_memo_idx: 上次插入研究备忘录后的 messages 长度（下次 memo 的起点）
        all_tool_results: 累积的 ToolResult.to_dict() 列表（JSON-able）
        rounds_log: 每轮日志条目列表
        model_used: 最近一次实际使用的模型 id
        status: running | awaiting_confirmation | done | failed
        pending_round: M2 用（人工确认待执行的工具轮）；本任务恒 None
        final_output: 终态文本输出（status=done 时填）
        evidence_refs: 证据引用快照（ReviewTool 写入；与 all_tool_results 区分——
                       这是经校验/筛选后用于综述的证据，state 内为单一真源，
                       由 save_state 持久化，避免列覆盖竞态）。
        validation_summary: 产出/引用校验汇总（Guardrails/一致性校验结果快照），
                            None 表示尚未校验。
        provenance_map: B4b/B4c 溯源映射快照（ReviewTool 写入）：occurrence anchor_id
                        → {paper_id, attachment_id, page_no, block_idx, bbox,
                        section_title, quote, ...}，供前端点击引用跳回原文。
                        None 表示尚未生成（旧快照容错）。
    """

    messages: list[dict]
    round_idx: int = 0
    tool_rounds: int = 0
    last_memo_idx: int = 2
    all_tool_results: list[dict] = field(default_factory=list)
    rounds_log: list[dict] = field(default_factory=list)
    model_used: str = ""
    status: str = "running"
    pending_round: dict | None = None
    final_output: str | None = None
    evidence_refs: list = field(default_factory=list)
    validation_summary: dict | None = None
    provenance_map: dict | None = None

    def to_json(self) -> dict:
        """序列化为 JSON-able dict（messages / all_tool_results 已是 JSON-able）。"""
        return {
            "messages": self.messages,
            "round_idx": self.round_idx,
            "tool_rounds": self.tool_rounds,
            "last_memo_idx": self.last_memo_idx,
            "all_tool_results": self.all_tool_results,
            "rounds_log": self.rounds_log,
            "model_used": self.model_used,
            "status": self.status,
            "pending_round": self.pending_round,
            "final_output": self.final_output,
            "evidence_refs": self.evidence_refs,
            "validation_summary": self.validation_summary,
            "provenance_map": self.provenance_map,
        }

    @classmethod
    def from_json(cls, d: dict) -> "LoopState":
        """从 to_json 产物重建 LoopState。

        对旧快照容错：缺 evidence_refs / validation_summary / provenance_map 键时
        分别默认 [] / None / None。
        """
        return cls(
            messages=d.get("messages", []),
            round_idx=d.get("round_idx", 0),
            tool_rounds=d.get("tool_rounds", 0),
            last_memo_idx=d.get("last_memo_idx", 2),
            all_tool_results=d.get("all_tool_results", []),
            rounds_log=d.get("rounds_log", []),
            model_used=d.get("model_used", ""),
            status=d.get("status", "running"),
            pending_round=d.get("pending_round"),
            final_output=d.get("final_output"),
            evidence_refs=d.get("evidence_refs") or [],
            validation_summary=d.get("validation_summary"),
            provenance_map=d.get("provenance_map"),
        )


async def step_once(
    state: LoopState,
    ctx: AgentContext,
    *,
    emit: Callable[[dict], Awaitable[None]],
    llm_override: OverrideLLMConfig | None = None,
    confirm_check: Callable[[dict], bool] | None = None,
    deadline: float | None = None,
) -> LoopState:
    """推进一轮，返回更新后的 state（in-place 修改后原样返回）。

    搬入 autonomous_loop 循环体的"一轮"逻辑：
      - is_final = state.round_idx >= ctx.max_rounds（或 deadline 已过 → 强制收尾轮）
      - is_final 时：注入 wrap_up user 提示、不传 tools；
      - trim → emit(LLM_START) → call_llm_with_fallback；
      - 无 tool_calls → 即时 append assistant、status='done'、final_output=content、
        emit(ROUND_COMPLETE is_final) → 返回;
      - 有 tool_calls → emit(TOOLS_START) → 工具执行/确认挂起（见下）→ 收集
        all_tool_results(to_dict()) → 一次性 append assistant + clean tool 消息 →
        rounds_log.append → emit(ROUND_COMPLETE) → memo 插入 → round_idx += 1 → 返回。

    M2 确认协议（codex P0/P1）：
      - confirm_check=None 且无 run_id/session_factory → 纯 M1 行为：整批 execute_tool_calls。
      - confirm_check=None 但有 run_id/session_factory → 逐个执行 + 写工具幂等短路（不挂起）。
      - confirm_check 提供 → 严格按原顺序逐个解决；遇写工具且 confirm_check 返回 True 即
        挂起（assistant 消息不写入 state.messages，仅存进 pending_round；状态置
        awaiting_confirmation；发 tool_confirm_required）。延迟 append 保证整轮 tool 响应
        齐备才落 messages。

    Args:
        state: 当前循环状态（会被修改并返回）
        ctx: 静态上下文（registry / llm_router / prompts / run_id / session_factory / ...）
        emit: 事件回调，所有事件经它发出（替代直接 publish_run_event）
        llm_override: per-request LLM 覆盖
        confirm_check: Callable[[dict], bool]，接收 {tool_id,action,tool_call_id,args}；
                       None 表示不拦截（M1/auto_confirm，全部直接执行）。
        deadline: 可选超时时刻（time.time() 基准）。已过且未到最终轮时强制本轮收尾，
                  保留 autonomous_loop 的 loop_deadline 语义。None 表示不检查。
    """
    config = get_config()
    registry: ToolRegistry = ctx.registry  # type: ignore[assignment]
    round_start = time.time()

    # 超时保护：deadline 已过且尚未到最终轮 → 强制把本轮当成收尾轮处理
    if deadline is not None and time.time() > deadline and state.round_idx < ctx.max_rounds:
        logger.warning("[Engine] Execution timeout, jumping to final round")
        state.round_idx = ctx.max_rounds

    is_final_round = state.round_idx >= ctx.max_rounds
    func_defs = registry.get_function_definitions(ctx.tool_ids)
    tools_payload = func_defs if not is_final_round else None

    if is_final_round:
        final_prompt = ctx.wrap_up_prompt or (
            "You have completed all data collection and analysis. "
            "Please output your final summary now.\n"
            "Output as a JSON object. Do not request any more tools."
        )
        state.messages.append({"role": "user", "content": final_prompt})

    # 上下文裁剪
    budget = config.context_limit - config.context_reserve
    state.messages = trim_messages_to_fit(state.messages, budget, ctx.importance_scores)

    # 发布 LLM 调用事件
    await emit({
        "type": EventType.LLM_START,
        "round": state.round_idx + 1,
        "is_final": is_final_round,
        "context_tokens": estimate_messages_tokens(state.messages),
    })

    # 调用 LLM（支持 per-request 覆盖）
    response, model_used = await call_llm_with_fallback(
        ctx.llm_router, ctx.model_names, state.messages, tools=tools_payload,
        override=llm_override,
    )
    state.model_used = model_used

    choice = response["choices"][0]
    message = choice["message"]

    thinking = message.get("content") or ""
    tool_calls = message.get("tool_calls")

    # 无工具调用 → 终态
    # 注：本轮无工具，assistant 消息在此处即时 append（不涉及确认挂起）。
    if not tool_calls:
        state.messages.append(message)
        content = thinking
        if not content.strip():
            content = message.get("reasoning_content") or ""
        state.rounds_log.append({
            "round": state.round_idx + 1,
            "thinking": content[:500],
            "tool_calls": [],
            "duration": round(time.time() - round_start, 2),
            "is_final": True,
        })
        await emit({
            "type": EventType.ROUND_COMPLETE,
            "round": state.round_idx + 1,
            "thinking": content[:300],
            "tool_calls": [],
            "tool_results": [],
            "duration": round(time.time() - round_start, 2),
            "is_final": True,
        })
        state.final_output = content
        state.status = "done"
        return state

    # 准备工具调用摘要
    tool_call_summaries = [
        {
            "id": tc.get("id", ""),
            "name": tc.get("function", {}).get("name", ""),
            "args_preview": tc.get("function", {}).get("arguments", "")[:200],
        }
        for tc in tool_calls
    ]

    await emit({
        "type": EventType.TOOLS_START,
        "round": state.round_idx + 1,
        "thinking": thinking[:300],
        "tool_calls": tool_call_summaries,
    })

    # ------------------------------------------------------------------
    # P3-2: 工具执行前把 live LoopState 注入 tool_context，建立"工具回写 state"通道。
    # ReviewTool 据此写 state.evidence_refs / state.validation_summary（state 单一真源，
    # 经 save_state 落 agent_run 列）。注入点在所有执行路径之前，三条路径都覆盖。
    # ------------------------------------------------------------------
    if isinstance(ctx.tool_context, dict):
        ctx.tool_context["state"] = state

    # ------------------------------------------------------------------
    # 工具执行 / 确认挂起（M2 codex P0/P1：in-order suspend + delayed append）
    #
    # confirm_check is None（M1 / auto_confirm）：保持今天行为——整批交给
    #   execute_tool_calls（写串行/读并发），再可选做幂等短路，最后一次性 append。
    # confirm_check 提供时：严格按原顺序逐个处理 tool_calls：
    #   - 读工具 → 立即执行、追加到 completed；
    #   - 写工具 且 confirm_check(call)=True → 停止迭代、挂起本轮（assistant 消息
    #     与 completed/剩余队列只存进 pending_round，不写 state.messages）。
    #   - 写工具 但 confirm_check(call)=False（已确认/auto）→ 立即执行。
    # ------------------------------------------------------------------
    if confirm_check is not None:
        suspended, tool_messages = await _resolve_round_with_confirm(
            state, ctx, config, message, tool_calls,
            confirm_check=confirm_check, emit=emit,
        )
        if suspended:
            return state
    elif ctx.session_factory is not None and ctx.run_id is not None:
        # confirm_check=None 但启用了幂等审计（如 auto_confirm 路径）：
        # 仍逐个执行以对写工具做"执行前查/执行后记"短路（confirm_check 恒 False → 不挂起）。
        _, tool_messages = await _resolve_round_with_confirm(
            state, ctx, config, message, tool_calls,
            confirm_check=lambda _call: False, emit=emit,
        )
    else:
        # 纯 M1 路径（无 run_id/session_factory）：保持今天行为，整批走 execute_tool_calls
        # （写串行/读并发），不做幂等审计。
        tool_messages = await execute_tool_calls(
            registry=registry,
            tool_calls=tool_calls,
            context=ctx.tool_context,
            concurrency=config.tool_concurrency,
            tool_timeout=config.tool_timeout,
            tool_result_max_chars=config.tool_result_max_chars,
            extra_params=ctx.extra_tool_params,
        )

    # 收集结果（all_tool_results 存 to_dict() 后的 JSON-able dict）
    tool_results_summary = []
    for msg in tool_messages:
        tr: ToolResult | None = msg.get("_tool_result")
        if tr:
            state.all_tool_results.append(tr.to_dict())
            tool_results_summary.append({
                "tool_id": tr.tool_id,
                "action": tr.action,
                "success": tr.success,
                "summary": (tr.summary or "")[:200],
                "data_source": tr.data_source or "",
                "error": (tr.error or "")[:100] if tr.error else None,
            })

    # 整轮完整解决（所有 tool 响应齐备）→ 此刻一次性 append assistant + tool 消息
    # （延迟 append：保证挂起时 assistant 消息不会先落进 state.messages）。
    state.messages.append(message)
    for msg in tool_messages:
        clean = {k: v for k, v in msg.items() if k != "_tool_result"}
        state.messages.append(clean)

    round_duration = round(time.time() - round_start, 2)

    round_entry = {
        "round": state.round_idx + 1,
        "thinking": thinking[:500],
        "tool_calls": tool_call_summaries,
        "tool_results": tool_results_summary,
        "duration": round_duration,
        "is_final": False,
    }
    state.rounds_log.append(round_entry)

    await emit({
        "type": EventType.ROUND_COMPLETE,
        **round_entry,
    })

    state.tool_rounds += 1

    # 中途研究备忘录
    memo_interval = config.memo_interval
    if state.tool_rounds > 0 and state.tool_rounds % memo_interval == 0:
        memo = build_research_memo(state.messages, state.last_memo_idx)
        if memo:
            state.messages.append({"role": "user", "content": memo})
            state.last_memo_idx = len(state.messages)
            logger.info(f"[Engine] Inserted research memo (round {state.round_idx + 1})")

    logger.info(
        f"[Engine] Round {state.round_idx + 1}: "
        f"{len(tool_calls)} tool_calls, "
        f"total {len(state.all_tool_results)} results"
    )

    state.round_idx += 1

    # 最终轮仍返回 tool_calls（LLM 无视 tools=None）→ 不再续轮，避免死循环。
    # 与旧 autonomous_loop 一致：range 耗尽后 fallback 取最后一条非空 assistant content。
    # thinking 为空时保持 final_output=None，交由调用方 fallback 反查更早的 assistant。
    if is_final_round:
        state.status = "done"
        if thinking.strip():
            state.final_output = thinking

    return state


# ======================================================================
# Autonomous Function Calling 循环（向后兼容包装，复用 step_once）
# ======================================================================

async def autonomous_loop(
    *,
    registry: ToolRegistry,
    llm_router: LLMRouter,
    model_names: list[str],
    system_prompt: str,
    user_prompt: str,
    tool_ids: set[str] | None = None,
    max_rounds: int = 3,
    run_id: str = "",
    publisher: EventPublisher | None = None,
    importance_scores: dict[str, int] | None = None,
    wrap_up_prompt: str | None = None,
    extra_tool_params: dict[str, Any] | None = None,
    context: Any = None,
    llm_override: OverrideLLMConfig | None = None,
) -> tuple[str, str, list[ToolResult], list[dict]]:
    """LLM 自主决策的多轮 function calling 循环

    Args:
        registry: 工具注册中心
        llm_router: LLM 路由器
        model_names: 模型优先级列表（fallback 链）
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        tool_ids: 限定可用工具集合，None 表示全部
        max_rounds: 最大工具调用轮数
        run_id: Run ID（用于事件推送）
        publisher: 事件发布器
        importance_scores: 工具重要性评分（用于上下文裁剪）
        wrap_up_prompt: 最终总结轮的提示词（None 使用默认）
        extra_tool_params: 注入到每个工具调用的额外参数
        context: 透传给工具的执行上下文
        llm_override: per-request LLM 配置覆盖（用户自带 key/base_url/model，不落盘）

    Returns:
        (final_text_output, model_used, all_tool_results, rounds_log)
    """
    config = get_config()
    publisher = publisher or NullEventPublisher()

    # 1) 初始 LoopState + AgentContext
    state = LoopState(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    ctx = AgentContext(
        registry=registry,
        llm_router=llm_router,
        model_names=model_names,
        system_prompt=system_prompt,
        tool_ids=tool_ids,
        max_rounds=max_rounds,
        wrap_up_prompt=wrap_up_prompt or "",
        importance_scores=importance_scores,
        extra_tool_params=extra_tool_params,
        tool_context=context,
    )

    # 运行期超时（保留原 loop_deadline 语义）
    deadline = (
        time.time()
        + config.loop_base_timeout
        + max_rounds * config.loop_per_round_timeout
    )

    # 2) emit 适配旧 publisher（调用方负责 run_id）
    async def emit(ev: dict) -> None:
        await publish_run_event(publisher, run_id, ev)

    # 先发 RUN_START（保持原有行为）
    await emit({
        "type": EventType.RUN_START,
        "max_rounds": max_rounds,
        "model": model_names[0] if model_names else "",
    })

    # 3) 单步推进直到非 running（max_rounds 上限语义由 step_once 的 is_final 处理）
    while state.status == "running":
        state = await step_once(
            state, ctx,
            emit=emit,
            llm_override=llm_override,
            confirm_check=None,
            deadline=deadline,
        )

    # 4) 还原返回契约
    tool_results = [ToolResult.from_dict(d) for d in state.all_tool_results]

    if state.final_output is not None:
        return state.final_output, state.model_used, tool_results, state.rounds_log

    # 兜底：未拿到 final_output（理论上 step_once 的最终轮总会产出）→ 取最后一条 assistant content
    for msg in reversed(state.messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"], state.model_used, tool_results, state.rounds_log

    raise ValueError(f"Autonomous loop: no valid output after {max_rounds} rounds")
