"""A4 · SubmitEvidencePackTool —— 价值核验 subagent 回传「可核验证据」。

铁律边界（仿 FS_Agent submit_evidence_pack）：本工具**只规整结构、不做算术、不裁决**。
最终价值 verdict 由 app/review/value_check.py 的确定性 resolver 出（decided_by=deterministic）。
LLM/subagent 只攒证据，绝不在此层产 verdict。

收集形态（领域无关）：
  reverse_search   : {query, provider, hits:[{title, year|null, doi|null, ...}]}  反向检索命中（去重在 resolver）
  biblio_structure : {metric, concept_a, concept_b, ...}                            计量结构线索（佐证，非裁决）
  notes            : [str]                                                          subagent 备注
  skipped          : [{reason}]                                                     找到但不敢采信的候选
"""
from __future__ import annotations

from typing import Any

from app.harness.tools import BaseTool, ToolResult


class SubmitEvidencePackTool(BaseTool):
    tool_id = "submit_evidence_pack"
    tool_name = "提交价值核验证据"
    description = (
        "提交研究方向价值核验的可核验证据（反向检索命中 / 计量结构线索 / 备注 / 跳过项）。"
        "只提交证据，不做算术、不产价值裁决——裁决由确定性 resolver 完成。"
    )
    actions = ["submit"]
    tags = ["write"]
    action_schemas = {
        "submit": {
            "type": "object",
            "properties": {
                "pack": {
                    "type": "object",
                    "description": (
                        "证据包，建议含 gap_id / reverse_search / biblio_structure / notes / skipped"
                    ),
                },
            },
            "required": ["pack"],
        },
    }

    async def _execute(self, action: str, params: dict[str, Any], context: Any = None) -> ToolResult:
        if action != "submit":
            return self._fail(action, f"未知 action: {action}")
        pack = params.get("pack")
        normalized = _normalize_pack(pack)
        if "error" in normalized:
            return self._fail(action, normalized["error"])  # fail-loud：非法证据包显式失败
        n = _count_entries(normalized)
        gid = normalized.get("gap_id") or "?"
        return self._ok(
            "submit", [normalized], source="subagent",
            summary=f"已接收 gap {gid} 证据包（{n} 条线索，不裁决）",
        )


# 仅规整结构、不解释、不裁决（铁律：collect-only）。
_LIST_KEYS = ("notes", "skipped")
# LLM 若在证据包里夹带"裁决"字段，一律剥离：价值 verdict 的唯一权威是确定性 resolver
# （decided_by=deterministic）。在工具层硬剥离，杜绝 LLM 裁决混入证据被下游误读（codex A3 P2）。
_FORBIDDEN_VERDICT_KEYS = ("verdict", "score", "decided_by", "thresholds", "rationale")


def _normalize_pack(pack: Any) -> dict[str, Any]:
    if not isinstance(pack, dict) or not pack:
        return {"error": "pack 必须是非空对象"}
    out = dict(pack)
    # gap_id 必填（fail-loud）：证据须能关联回被核验的 GAP，否则 resolver 无法消费、
    # 摘要只剩 '?'（codex A3 二审 P2）。
    gid = out.get("gap_id")
    if not str(gid or "").strip():
        return {"error": "pack 必须含非空 gap_id（证据须关联到被核验的 GAP）"}
    out["gathered_by"] = "subagent"
    # collect-only 边界：剥离任何 LLM 夹带的裁决字段（保留证据本身，不丢数据）。
    stripped = [k for k in _FORBIDDEN_VERDICT_KEYS if k in out]
    for k in stripped:
        out.pop(k, None)
    if stripped:
        out["_stripped_verdict_fields"] = stripped  # 透明留痕：剥离了什么
    # notes/skipped 容错：LLM 可能按 SOP 提交单条字符串而非数组 → 规整为单元素数组
    # （逐字保留内容，不丢不改），避免合规提交被误拒为 collect 失败（codex A3 二审 P2）。
    for key in _LIST_KEYS:
        v = out.get(key)
        if v is None:
            continue
        if isinstance(v, str):
            out[key] = [v]
        elif not isinstance(v, list):
            return {"error": f"{key} 必须是字符串或数组"}
    # reverse_search.hits / biblio_structure 形状容错：仅校验类型，不改值（逐字保留）。
    rs = out.get("reverse_search")
    if rs is not None and not isinstance(rs, dict):
        return {"error": "reverse_search 必须是对象"}
    if isinstance(rs, dict) and rs.get("hits") is not None and not isinstance(rs.get("hits"), list):
        return {"error": "reverse_search.hits 必须是数组"}
    # reverse_search 必带非空 query：否则下游无法核验"到底检索了什么"，0 命中也无法判真空白
    # （codex A3 二审 P2）。provider 缺省由 resolver 侧按默认处理。
    if isinstance(rs, dict) and not str(rs.get("query") or "").strip():
        return {"error": "reverse_search 须带非空 query（便于核验检索了什么；0 命中可，但须有检索式）"}
    bs = out.get("biblio_structure")
    if bs is not None and not isinstance(bs, dict):
        return {"error": "biblio_structure 必须是对象"}
    # 拒绝空证据包（仅 gap_id、无任何证据）：否则下游把"空包成功"误当有效产出（codex A3 二审 P2）。
    # 注意：reverse_search 已执行（有 query 或 hits 键，即便 0 命中）本身就是"真空白"证据，
    # 故不要求 hits 非空——只要求确有一类证据被收集。
    if not _has_evidence(out):
        return {"error": "空证据包：至少需 reverse_search(已检索)/biblio_structure/notes/skipped 之一"}
    return out


def _has_evidence(pack: dict[str, Any]) -> bool:
    """是否承载至少一类证据（反向检索已执行 / 计量结构 / notes / skipped）。"""
    rs = pack.get("reverse_search")
    if isinstance(rs, dict) and str(rs.get("query") or "").strip():
        return True   # 执行了带检索式的反向检索（0 命中也是"真空白"证据）
    if isinstance(pack.get("biblio_structure"), dict) and pack["biblio_structure"]:
        return True
    if pack.get("notes"):
        return True
    if pack.get("skipped"):
        return True
    return False


def _count_entries(pack: dict[str, Any]) -> int:
    total = 0
    rs = pack.get("reverse_search")
    if isinstance(rs, dict) and isinstance(rs.get("hits"), list):
        total += len(rs["hits"])
    for key in _LIST_KEYS:
        v = pack.get(key)
        if isinstance(v, list):
            total += len(v)
    if isinstance(pack.get("biblio_structure"), dict):
        total += 1
    return total
