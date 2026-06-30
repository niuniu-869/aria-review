"""A1 · ScratchpadTool — scratchpad 的 BaseTool 封装（agent 在 GAP run 内反复调）。

actions: add / update / list。tags=['write']（写工具，registry 串行执行）。
铁律：工具只存取结构化条目、不裁决；add 无 ≥1 supporting_papers → 返回 success=False
（fail-loud，非静默空）。结构化结果走 ToolResult.data（dispatch collect_tool_id 据此收集）。

scratchpad 来源（按优先级）：
  1) context['scratchpad']：上层编排注入的 Scratchpad 实例（首选，A5/测试用）。
  2) context['run_id'] + context['session_factory']：按需构建 DbScratchpadStore 兜底。
两者皆无 → fail（无可用 scratchpad，fail-loud）。
"""
from __future__ import annotations

from typing import Any

from app.agent.scratchpad import (
    DbScratchpadStore,
    Scratchpad,
    ScratchpadError,
)
from app.harness.tools import BaseTool, ToolResult


class ScratchpadTool(BaseTool):
    tool_id = "scratchpad"
    tool_name = "GAP 工作记忆"
    description = (
        "在一次 GAP 发现 run 内累积/更新结构化 GAP 候选（研究空白论断 + 逐字溯源支撑）。"
        "只存取条目，不做价值裁决；新增条目必须带至少一条含 anchor_id 的支撑证据。"
    )
    actions = ["add", "update", "list"]
    tags = ["write"]
    action_schemas = {
        "add": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "所属主题簇"},
                "statement": {
                    "type": "string",
                    "description": "GAP 论断，如 'X 与 Y 的关系在 Z 情境下未被研究'",
                },
                "lens": {
                    "type": "string", "enum": ["concept", "method", "theory"],
                    "description": "空白视角：概念/方法/理论",
                },
                "supporting_papers": {
                    "type": "array",
                    "description": "支撑证据（至少 1 条），每条 {paper_id, anchor_id, quote}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "paper_id": {"type": "integer"},
                            "anchor_id": {"type": "string"},
                            "quote": {"type": "string"},
                        },
                        "required": ["paper_id", "anchor_id"],
                    },
                },
                "counter_evidence": {
                    "type": "array",
                    "description": "反证（可空），每条 {paper_id, anchor_id, note}",
                    "items": {"type": "object"},
                },
                "confidence": {"type": "number", "description": "LLM 自评（仅供排序，非裁决依据）"},
            },
            "required": ["statement", "lens", "supporting_papers"],
        },
        "update": {
            "type": "object",
            "properties": {
                "gap_id": {"type": "string"},
                "statement": {"type": "string"},
                "theme": {"type": "string"},
                "lens": {"type": "string", "enum": ["concept", "method", "theory"]},
                "status": {
                    "type": "string",
                    "enum": ["draft", "verified", "accepted", "rejected"],
                },
                "confidence": {"type": "number"},
            },
            "required": ["gap_id"],
        },
        "list": {"type": "object", "properties": {}, "required": []},
    }

    @staticmethod
    def _resolve_pad(context: Any) -> Scratchpad | None:
        if not isinstance(context, dict):
            return None
        pad = context.get("scratchpad")
        if isinstance(pad, Scratchpad):
            return pad
        run_id = context.get("run_id")
        sf = context.get("session_factory")
        if run_id is not None and sf is not None:
            return Scratchpad(
                str(run_id),
                DbScratchpadStore(sf, project_id=context.get("project_id")),
            )
        return None

    async def _execute(self, action: str, params: dict[str, Any], context: Any = None) -> ToolResult:
        pad = self._resolve_pad(context)
        if pad is None:
            return self._fail(action, "无可用 scratchpad（context 缺 scratchpad / run_id+session_factory）")

        try:
            if action == "add":
                entry = await pad.add(
                    theme=params.get("theme", ""),
                    statement=params.get("statement", ""),
                    lens=params.get("lens", ""),
                    supporting_papers=params.get("supporting_papers"),
                    counter_evidence=params.get("counter_evidence"),
                    confidence=params.get("confidence", 0.0),
                )
                return self._ok("add", [entry.to_dict()], source="scratchpad",
                                summary=f"已记录 GAP 候选 {entry.gap_id}（{entry.lens}）")
            if action == "update":
                gap_id = params.get("gap_id")
                if not gap_id:
                    return self._fail("update", "缺少 gap_id")
                changes = {k: v for k, v in params.items() if k != "gap_id"}
                entry = await pad.update(gap_id, **changes)
                return self._ok("update", [entry.to_dict()], source="scratchpad",
                                summary=f"已更新 {entry.gap_id} → status={entry.status}")
            if action == "list":
                entries = await pad.list()
                return self._ok("list", [e.to_dict() for e in entries], source="scratchpad",
                                summary=f"当前 scratchpad 共 {len(entries)} 条 GAP 候选")
        except ScratchpadError as e:
            # fail-loud：违反铁律的写入显式失败，不静默落条目。
            return self._fail(action, str(e))

        return self._fail(action, f"未知 action: {action}")
