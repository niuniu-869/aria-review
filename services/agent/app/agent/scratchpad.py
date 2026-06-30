"""A1 · scratchpad — 类 harness 的 in-run 结构化工作记忆（GAP 底座）。

设计依据：
  - 条目 = GapCandidate（契约 §2.2 字段级一致），是 agent 在一次 GAP run 内反复读写的
    结构化工作记忆：既喂回 LLM 下一轮，又实时浮现给人（HITL），又作价值核验输入。
  - 工具只存取结构化条目，**不做裁决**。
  - fail-loud：`add` 必须带 ≥1 supporting_papers（含 paper_id + anchor_id），否则拒该条
    并显式抛 ScratchpadError，绝不静默落空条目（铁律 §5）。
  - 持久化经 ScratchpadStore 协议解耦：InMemoryScratchpadStore（测试/轻量）/
    DbScratchpadStore（落 gap_candidate 表，供 GET .../scratchpad 与 verify/HITL 复用）。

领域无关：本模块零商科/会计词，lens/status 为通用枚举，可跨 5 领域（含 2 工程）。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

# 受控枚举。领域无关。
LENSES = ("concept", "method", "theory")
STATUSES = ("draft", "verified", "accepted", "rejected")

# update 允许改写的字段白名单（防止越权写 gap_id/run_id 等）。
_UPDATABLE = {
    "theme", "statement", "lens", "supporting_papers",
    "counter_evidence", "confidence", "status", "value_verdict",
}


class ScratchpadError(ValueError):
    """scratchpad 写入违反铁律（空证据 / 缺 anchor / 非法枚举 / 未知 gap_id）。

    显式异常 = fail-loud：调用方（工具层）据此返回 ToolResult(success=False)，
    绝不把违规静默成"成功落条目"。
    """


# ----------------------------------------------------------------- 校验

def validate_supporting_papers(items: Any) -> list[dict]:
    """校验并归一化 supporting_papers（fail-loud）。

    规则：非空 list；每条须含 paper_id（可转 int）+ anchor_id（非空 str）；quote 可选。
    归一化为 {paper_id:int, anchor_id:str, quote:str}。
    """
    if not isinstance(items, list) or not items:
        raise ScratchpadError("supporting_papers 必须为非空数组（至少 1 条带源坐标的支撑证据）")
    out: list[dict] = []
    for i, sp in enumerate(items):
        if not isinstance(sp, dict):
            raise ScratchpadError(f"supporting_papers[{i}] 必须是对象")
        pid = sp.get("paper_id")
        anchor = sp.get("anchor_id")
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            raise ScratchpadError(f"supporting_papers[{i}].paper_id 缺失或非整数: {pid!r}")
        if not isinstance(anchor, str) or not anchor.strip():
            raise ScratchpadError(
                f"supporting_papers[{i}].anchor_id 缺失（逐字溯源必须带源坐标 anchor_id）")
        out.append({
            "paper_id": pid_int,
            "anchor_id": anchor.strip(),
            "quote": str(sp.get("quote") or ""),
        })
    return out


def _validate_counter_evidence(items: Any) -> list[dict]:
    """counter_evidence 可空；非空时每条须含 paper_id + anchor_id（与支撑证据同口径溯源）。"""
    if items is None:
        return []
    if not isinstance(items, list):
        raise ScratchpadError("counter_evidence 必须是数组")
    out: list[dict] = []
    for i, ce in enumerate(items):
        if not isinstance(ce, dict):
            raise ScratchpadError(f"counter_evidence[{i}] 必须是对象")
        try:
            pid_int = int(ce.get("paper_id"))
        except (TypeError, ValueError):
            raise ScratchpadError(f"counter_evidence[{i}].paper_id 缺失或非整数")
        anchor = ce.get("anchor_id")
        if not isinstance(anchor, str) or not anchor.strip():
            raise ScratchpadError(f"counter_evidence[{i}].anchor_id 缺失")
        out.append({
            "paper_id": pid_int,
            "anchor_id": anchor.strip(),
            "note": str(ce.get("note") or ""),
        })
    return out


# ----------------------------------------------------------------- 数据结构

@dataclass
class GapCandidate:
    """GAP 候选（契约 §2.2 字段级）。value_verdict 由 A4 确定性 resolver 写入，非本层裁决。"""

    gap_id: str
    theme: str
    statement: str
    lens: str
    supporting_papers: list[dict]
    counter_evidence: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "draft"
    value_verdict: dict | None = None

    def to_dict(self) -> dict:
        return {
            "gap_id": self.gap_id,
            "theme": self.theme,
            "statement": self.statement,
            "lens": self.lens,
            "supporting_papers": self.supporting_papers,
            "counter_evidence": self.counter_evidence,
            "confidence": self.confidence,
            "status": self.status,
            "value_verdict": self.value_verdict,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GapCandidate":
        return cls(
            gap_id=str(d["gap_id"]),
            theme=str(d.get("theme") or ""),
            statement=str(d.get("statement") or ""),
            lens=str(d.get("lens") or "concept"),
            supporting_papers=list(d.get("supporting_papers") or []),
            counter_evidence=list(d.get("counter_evidence") or []),
            confidence=float(d.get("confidence") or 0.0),
            status=str(d.get("status") or "draft"),
            value_verdict=d.get("value_verdict"),
        )


# ----------------------------------------------------------------- 存储协议

class ScratchpadStore(Protocol):
    """scratchpad 持久化协议 —— 实现此接口对接不同后端（内存 / DB）。"""

    async def upsert(self, run_id: str, entry: GapCandidate) -> None: ...

    async def get(self, run_id: str, gap_id: str) -> GapCandidate | None: ...

    async def list(self, run_id: str) -> list[GapCandidate]: ...


class InMemoryScratchpadStore:
    """进程内 scratchpad 存储（测试/轻量）。按 run_id 分桶，插入序保持。"""

    def __init__(self) -> None:
        # run_id -> {gap_id -> GapCandidate}（dict 在 py3.7+ 保插入序）
        self._buckets: dict[str, dict[str, GapCandidate]] = {}

    async def upsert(self, run_id: str, entry: GapCandidate) -> None:
        self._buckets.setdefault(run_id, {})[entry.gap_id] = entry

    async def get(self, run_id: str, gap_id: str) -> GapCandidate | None:
        return self._buckets.get(run_id, {}).get(gap_id)

    async def list(self, run_id: str) -> list[GapCandidate]:
        return list(self._buckets.get(run_id, {}).values())


class DbScratchpadStore:
    """落 gap_candidate 表的 scratchpad 存储（生产 + GET/verify/HITL 复用）。

    经 repositories.gaps 读写；每次操作开独立 session（session_factory）。
    并发安全：gap_id 服务端唯一生成 → 并发 add 互不撞键；update 在单 session 内读改写。
    """

    def __init__(self, session_factory: Any, project_id: int | None = None) -> None:
        self._session_factory = session_factory
        self._project_id = project_id

    async def upsert(self, run_id: str, entry: GapCandidate) -> None:
        from ..repositories import gaps as gaps_repo
        async with self._session_factory() as s:
            await gaps_repo.upsert_gap(s, run_id, entry, project_id=self._project_id)

    async def get(self, run_id: str, gap_id: str) -> GapCandidate | None:
        from ..repositories import gaps as gaps_repo
        async with self._session_factory() as s:
            return await gaps_repo.get_gap_in_run(s, run_id, gap_id)

    async def list(self, run_id: str) -> list[GapCandidate]:
        from ..repositories import gaps as gaps_repo
        async with self._session_factory() as s:
            return await gaps_repo.list_gaps_by_run(s, run_id)


# ----------------------------------------------------------------- Scratchpad

class Scratchpad:
    """一次 GAP run 的结构化工作记忆。add/update/list 三动作，asyncio.Lock 保一致。

    project_id 可选：A5 编排传入以建立 gap↔project 归属（落库）；A1 单测可不传。
    """

    def __init__(self, run_id: str, store: ScratchpadStore, project_id: int | None = None) -> None:
        self.run_id = str(run_id)
        self.store = store
        self.project_id = project_id
        self._lock = asyncio.Lock()

    @staticmethod
    def _new_gap_id() -> str:
        return f"gap_{uuid.uuid4().hex[:12]}"

    async def add(
        self,
        *,
        theme: str,
        statement: str,
        lens: str,
        supporting_papers: list[dict],
        counter_evidence: list[dict] | None = None,
        confidence: float = 0.0,
        gap_id: str | None = None,
    ) -> GapCandidate:
        """新增一条 GAP 候选（status=draft）。fail-loud：证据/枚举非法直接抛 ScratchpadError。"""
        if lens not in LENSES:
            raise ScratchpadError(f"lens 非法: {lens!r}，须为 {LENSES}")
        sp = validate_supporting_papers(supporting_papers)
        ce = _validate_counter_evidence(counter_evidence)
        if not str(statement).strip():
            raise ScratchpadError("statement 不能为空")
        entry = GapCandidate(
            gap_id=gap_id or self._new_gap_id(),
            theme=str(theme or ""),
            statement=str(statement),
            lens=lens,
            supporting_papers=sp,
            counter_evidence=ce,
            confidence=float(confidence or 0.0),
            status="draft",
            value_verdict=None,
        )
        async with self._lock:
            await self.store.upsert(self.run_id, entry)
        return entry

    async def update(self, gap_id: str, **changes: Any) -> GapCandidate:
        """改写一条 GAP 候选的允许字段。未知 gap_id / 非法字段值 → fail-loud。"""
        bad = set(changes) - _UPDATABLE
        if bad:
            raise ScratchpadError(f"不允许更新字段: {sorted(bad)}")
        async with self._lock:
            entry = await self.store.get(self.run_id, gap_id)
            if entry is None:
                raise ScratchpadError(f"未知 gap_id: {gap_id!r}（run={self.run_id}）")
            if "lens" in changes and changes["lens"] not in LENSES:
                raise ScratchpadError(f"lens 非法: {changes['lens']!r}")
            if "status" in changes and changes["status"] not in STATUSES:
                raise ScratchpadError(f"status 非法: {changes['status']!r}")
            if "supporting_papers" in changes:
                changes["supporting_papers"] = validate_supporting_papers(changes["supporting_papers"])
            if "counter_evidence" in changes:
                changes["counter_evidence"] = _validate_counter_evidence(changes["counter_evidence"])
            for k, v in changes.items():
                setattr(entry, k, v)
            await self.store.upsert(self.run_id, entry)
            return entry

    async def list(self) -> list[GapCandidate]:
        return await self.store.list(self.run_id)
