"""写工具确认协议 + 幂等。
正确性边界：跨 resume/重放"不产生重复副作用"由业务唯一约束保证（library dedup_key /
uq_project_paper / corpus content_hash / project name 唯一）。本模块 ToolInvocation 仅审计 +
执行前短路。残留崩溃窗口：业务 commit 成功、record 前崩溃 → resume 重放；对幂等写无害,
对非幂等创建由业务唯一约束兜底。单进程单用户 demo 接受。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ToolInvocation


def make_idempotency_key(
    run_id: int, round_idx: int, tool_id: str, action: str, args: dict[str, Any],
) -> str:
    """对 (run_id, round_idx, tool_id, action, args) 算稳定幂等键（前 32 hex）。

    args 经 sort_keys 规范化 → 键序无关、内容敏感。同一轮同一调用重放得同键。
    """
    payload = json.dumps(
        {"r": run_id, "i": round_idx, "t": tool_id, "a": action, "args": args},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def needs_confirmation(registry, tool_id: str, auto_confirm: bool) -> bool:
    """是否需要人工确认：写工具 且 未开 auto_confirm。"""
    return registry.is_write_tool(tool_id) and not auto_confirm


async def get_invocation(s: AsyncSession, run_id: int, key: str) -> dict | None:
    """执行前查：命中同 (run_id,key) 返回其 result（调用方据此跳过执行、复用结果）；未命中 None。"""
    return (await s.execute(select(ToolInvocation.result).where(
        ToolInvocation.run_id == run_id, ToolInvocation.idempotency_key == key))).scalar_one_or_none()


async def record_invocation(s, run_id, key, tool_id, action, result) -> tuple[ToolInvocation, bool]:
    """执行后记。并发撞唯一约束 → 回滚重查返回已有与 False。"""
    inv = ToolInvocation(run_id=run_id, idempotency_key=key, tool_id=tool_id, action=action, result=result)
    s.add(inv)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        existing = (await s.execute(select(ToolInvocation).where(
            ToolInvocation.run_id == run_id, ToolInvocation.idempotency_key == key))).scalar_one()
        return existing, False
    await s.refresh(inv)
    return inv, True
