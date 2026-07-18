"""P2 · feasibility 二次验证 —— feasibility-scout subagent 攒证 + 确定性状态机裁决。

铁律（同 value_check，v3 校正）：
  - LLM 只攒证据 / 确定性裁决（decided_by="deterministic"）。
  - **状态机裁决，不用 hit_count 门槛 / 浮点分做主判**（codex P1-1 去伪精确）。
  - data 不做一票否决：只有明确 unavailable 才 blocked；unknown 最多 hard（codex P0-1）。
  - novelty×feasibility 解耦：method 证据来自 component building_blocks（非完整 GAP 论断检索）；
    method_base.query 疑含完整 GAP 论断（工具已留痕）→ 不计 supported（codex P0-2）。领域无关。

裁决状态机（纯函数）：
  blocked   ← data_status=="unavailable" OR method_status=="blocked"        # 只认明确 blocker
  buildable ← data available AND method supported AND resource!="heavy"
  hard      ← 其余（大量 unknown 落这里，绝不误判 blocked / buildable）
"""
from __future__ import annotations

import logging
from typing import Any

from app.agent.dispatch import OUTCOME_OK, dispatch_to_skill

logger = logging.getLogger("agent.review.feasibility_check")

FEASIBILITY_SCOUT_SKILL = "feasibility-scout"


class FeasibilityCheckError(RuntimeError):
    """feasibility 核验失败（subagent 非 ok / 证据包非法）——fail-loud，绝不捏造 verdict。"""


# ---- 状态判定（纯函数，状态语义非计数） ----

def _data_status(pack: dict) -> tuple[str, int, int]:
    """data_status ∈ {available, unknown, unavailable}。返回 (status, dataset_count, open_count)。

    unavailable ← 有明确 data_unavailable 负证据（所需数据 proprietary/失效）——优先（保守 blocker）。
    available   ← 有 dataset access=="open" 且带明确可访问证据（url/仓库/license）。
    unknown     ← 其余（含「命中数据名但无可访问证据」——诚实，绝不冒充可得）。
    """
    da = pack.get("data_availability")
    datasets = (da.get("datasets") if isinstance(da, dict) else None) or []
    dataset_count = len(datasets) if isinstance(datasets, list) else 0
    open_count = 0
    for d in (datasets if isinstance(datasets, list) else []):
        if not isinstance(d, dict):
            continue
        access = str(d.get("access") or "").lower()
        # 明确可访问证据（codex P2-2 收紧，诚实）：必须有可解析的**链接**——url 字段，或 source
        # 里含 http/仓库标记（github/huggingface/kaggle/zenodo）。纯自由文本 source（只是「某论文
        # 提到该数据集」）**不算** available，绝不冒充可得。
        url = str(d.get("url") or "").strip()
        source = str(d.get("source") or "").strip().lower()
        has_access_url = bool(url) or any(
            m in source for m in ("http", "github.com", "huggingface", "kaggle", "zenodo", "physionet")
        )
        if access == "open" and has_access_url:
            open_count += 1
    if _has_negative(pack, {"data_unavailable"}):
        return "unavailable", dataset_count, open_count
    if open_count >= 1:
        return "available", dataset_count, open_count
    return "unknown", dataset_count, open_count


def _dedup_building_blocks(pack: dict) -> int:
    mb = pack.get("method_base")
    blocks = (mb.get("building_blocks") if isinstance(mb, dict) else None) or []
    seen: set[str] = set()
    for b in (blocks if isinstance(blocks, list) else []):
        if not isinstance(b, dict):
            continue
        name = str(b.get("name") or "").strip().lower()
        if name:
            seen.add(name)
    return len(seen)


def _method_query_suspected(pack: dict) -> bool:
    """method_base.query 是否疑含完整 GAP 论断（工具已在 _suspected_gap_statement_queries 留痕）。"""
    suspected = pack.get("_suspected_gap_statement_queries") or []
    mb = pack.get("method_base")
    mq = str((mb.get("query") if isinstance(mb, dict) else "") or "")
    return bool(mq) and mq in suspected


def _method_status(pack: dict) -> tuple[str, int]:
    """method_status ∈ {supported, unknown, blocked}。返回 (status, dedup_block_count)。

    blocked   ← 明确负证据（no_measurement / unidentifiable）——优先。
    supported ← ≥2 条去重 component building_blocks，且 method query 未疑含完整 GAP 论断
                （解耦命门：novelty 的检索不算 method 成熟证据）。
    unknown   ← 其余（不压死 buildability）。
    """
    n = _dedup_building_blocks(pack)
    if _has_negative(pack, {"no_measurement", "unidentifiable"}):
        return "blocked", n
    if n >= 2 and not _method_query_suspected(pack):
        return "supported", n
    return "unknown", n


def _resource_status(pack: dict) -> str:
    """resource_status ∈ {modest, heavy, unknown}（取 resource_scale.scale_flag，非法→unknown）。"""
    rc = pack.get("resource_scale")
    flag = str((rc.get("scale_flag") if isinstance(rc, dict) else "") or "").lower()
    return flag if flag in ("modest", "heavy", "unknown") else "unknown"


def _has_negative(pack: dict, kinds: set[str]) -> bool:
    for ne in (pack.get("negative_evidence") or []):
        if isinstance(ne, dict) and str(ne.get("kind") or "") in kinds:
            return True
    return False


def resolve_feasibility_verdict(pack: dict, config: dict | None = None) -> dict:
    """从 FeasibilityPack 出 FeasibilityVerdict（状态机，decided_by=deterministic，无浮点主判）。

    纯函数、可单测。config 预留（当前无阈值，状态机不需要）。
    """
    if not isinstance(pack, dict):
        raise FeasibilityCheckError("feasibility pack 必须是 dict")
    gap_id = str(pack.get("gap_id") or "")
    if not gap_id:
        raise FeasibilityCheckError("feasibility pack 缺 gap_id（无法裁决，fail-loud）")

    data_status, dataset_count, open_count = _data_status(pack)
    method_status, block_count = _method_status(pack)
    resource_status = _resource_status(pack)

    # 状态机（只认明确 blocker；大量 unknown 落 hard，绝不误 blocked/buildable）。
    if data_status == "unavailable" or method_status == "blocked":
        verdict = "blocked"
        rationale = (
            f"明确不可行 blocker：data_status={data_status} / method_status={method_status}"
            f"（只有明确负证据才 blocked）。"
        )
    elif data_status == "available" and method_status == "supported" and resource_status != "heavy":
        verdict = "buildable"
        rationale = (
            "数据有明确可访问证据、方法组件基座 ≥2 条且非重资源 → 方向可做（buildable）。"
        )
    else:
        verdict = "hard"
        rationale = (
            f"证据未齐（data={data_status}, method={method_status}, resource={resource_status}）："
            f"存在不确定项，判为有难度（hard），不误判为不可做或已就绪。"
        )

    return {
        "gap_id": gap_id,
        "verdict": verdict,
        "data_status": data_status,
        "method_status": method_status,
        "resource_status": resource_status,
        "rationale": rationale,
        "decided_by": "deterministic",
        "signals": {
            "data_status": data_status,
            "method_status": method_status,
            "resource_status": resource_status,
            "dataset_count": dataset_count,
            "open_dataset_count": open_count,
            "dedup_building_blocks": block_count,
            "method_query_suspected": _method_query_suspected(pack),
            "negative_kinds": sorted({
                str(ne.get("kind") or "") for ne in (pack.get("negative_evidence") or [])
                if isinstance(ne, dict)
            }),
        },
    }


def _merge_entry(existing: dict, new: dict, prefer_keys: tuple[str, ...]) -> None:
    """同名条目字段级合并（codex P2-1）：优先保留非空 prefer_keys（url/source/doi/has_code/access），
    不 first-wins 丢更完整的证据。existing 原地更新。"""
    for k in prefer_keys:
        if not str(existing.get(k) or "").strip() and str(new.get(k) or "").strip():
            existing[k] = new[k]
    for k, v in new.items():
        if k not in existing or existing.get(k) in (None, "", []):
            existing[k] = v


def _merge_resource(flags: list[str]) -> str:
    """资源规模保守合并（codex P1-2）：只要有一份 heavy 即 heavy（防丢 heavy 误判 buildable）；
    否则 modest 优先于 unknown（modest 是明确正信号）。"""
    fs = {str(f or "").lower() for f in flags}
    if "heavy" in fs:
        return "heavy"
    if "modest" in fs:
        return "modest"
    return "unknown"


def _assemble_pack(gap_id: str, data: list[dict]) -> dict:
    """把 feasibility-scout 回传的多份 pack 合成一份。

    codex 修：(P1-1) 疑似含完整 GAP 论断 query 的 pack，其 building_blocks **不并入** clean 集
    （否则洗白成 supported、破坏解耦）；(P1-2) 资源保守合并 heavy 优先；(P2-1) 同名条目字段级
    合并、优先保留带 url/source/doi/has_code 的更强证据，不 first-wins 丢证据。
    """
    datasets: dict[str, dict] = {}   # name → merged dataset
    blocks: dict[str, dict] = {}     # name → merged building_block（仅来自 clean query）
    negatives: list[dict] = []
    notes: list[str] = []
    skipped: list[dict] = []
    suspected: list[str] = []
    resource_flags: list[str] = []
    data_query = method_query = data_provider = method_provider = None

    for pack in (data or []):
        if not isinstance(pack, dict):
            continue
        pack_suspected = set(str(q) for q in (pack.get("_suspected_gap_statement_queries") or []))
        for sq in pack_suspected:
            suspected.append(sq)

        da = pack.get("data_availability")
        if isinstance(da, dict):
            data_query = data_query or da.get("query")
            data_provider = data_provider or da.get("provider")
            for d in (da.get("datasets") or []):
                if not isinstance(d, dict):
                    continue
                k = str(d.get("name") or "").strip().lower() or str(d.get("url") or "").strip()
                if not k:
                    continue
                if k in datasets:
                    _merge_entry(datasets[k], d, ("url", "source", "access", "kind"))
                else:
                    datasets[k] = dict(d)

        mb = pack.get("method_base")
        if isinstance(mb, dict):
            method_query = method_query or mb.get("query")
            method_provider = method_provider or mb.get("provider")
            # 解耦命门：本 pack 的 method query 疑含完整 GAP 论断 → 其 building_blocks 是被
            # novelty 检索污染的证据，**不并入** clean 集（保留在 suspected 留痕）。
            mb_query_suspected = str(mb.get("query") or "") in pack_suspected
            if not mb_query_suspected:
                for b in (mb.get("building_blocks") or []):
                    if not isinstance(b, dict):
                        continue
                    k = str(b.get("name") or "").strip().lower()
                    if not k:
                        continue
                    if k in blocks:
                        _merge_entry(blocks[k], b, ("doi", "has_code", "kind"))
                    else:
                        blocks[k] = dict(b)

        rc = pack.get("resource_scale")
        if isinstance(rc, dict) and rc.get("scale_flag"):
            resource_flags.append(str(rc.get("scale_flag")))
        for ne in (pack.get("negative_evidence") or []):
            if isinstance(ne, dict):
                negatives.append(ne)
        for n in (pack.get("notes") or []):
            notes.append(str(n))
        for sk in (pack.get("skipped") or []):
            if isinstance(sk, dict):
                skipped.append(sk)

    assembled: dict[str, Any] = {
        "gap_id": gap_id,
        "data_availability": {"query": data_query, "provider": data_provider,
                              "datasets": list(datasets.values())},
        "method_base": {"query": method_query, "provider": method_provider,
                        "building_blocks": list(blocks.values())},
        "resource_scale": {"scale_flag": _merge_resource(resource_flags)},
        "negative_evidence": negatives,
        "notes": notes,
        "skipped": skipped,
        "gathered_by": "subagent",
    }
    if suspected:
        assembled["_suspected_gap_statement_queries"] = suspected
    return assembled


async def verify_gap_feasibility(
    gap: dict,
    *,
    registry: Any,
    llm_router: Any,
    base_context: dict,
    config: dict | None = None,
    depth: int = 0,
    deadline: float | None = None,
    publisher: Any = None,
    llm_override: Any = None,
) -> dict:
    """对一条 GapCandidate 做可行性核验，返回 {gap_id, verdict, pack}。

    流程：派 feasibility-scout（最小授权 read_paper+search+submit_feasibility_pack）攒 data/method/
    resource 证据 → 合成去重 pack → resolve_feasibility_verdict 状态机裁决。与 value 链路独立，
    feasibility 失败不应影响既有 value_verdict（由调用方分离写库）。
    fail-loud：subagent 非 ok → 抛 FeasibilityCheckError。
    """
    gap_id = str(gap.get("gap_id") or "")
    statement = str(gap.get("statement") or "")
    theme = str(gap.get("theme") or "")
    # 生产 job 18 实证：不显式给白名单，scout 会拿 search 结果里的外部 paper_id 去
    # read_paper（必失败），3 次失败+未提交即 fail-loud。白名单=GAP 支撑文献的项目内 id。
    supporting_ids = [
        sp.get("paper_id") for sp in (gap.get("supporting_papers") or [])
        if isinstance(sp, dict) and sp.get("paper_id") is not None
    ]

    task = (
        f"针对以下研究空白（GAP）做**可行性侦察**（不是新颖性）：判断这个方向**做不做得出来**，"
        f"调 submit_feasibility_pack 回传证据，绝不下结论。\n"
        f"主题: {theme}\nGAP 论断: {statement}\n"
        f"把论断**拆成方法要素与数据要素的组件词**去检索（方法家族/工具/模型/库/数据类型），"
        f"**严禁把整句 GAP 论断或「A×B 是否被研究」拼进 query**（那是 novelty 的事，会泄漏进可行性）。"
        f"评估：数据可得性（有明确可访问证据才算 available）、方法组件基座（≥2 条可复用组件）、"
        f"资源规模、负证据（数据不可得/无可行测量/不可识别）。\n"
        f"read_paper 白名单（仅这些项目内 paper_id 可读，通常无需读）：{supporting_ids or '（空）'}；"
        f"search 检索结果中的文献一律**不可** read_paper——它们不在本项目内，直接把检索摘要记为证据。"
    )
    result = await dispatch_to_skill(
        skill_id=FEASIBILITY_SCOUT_SKILL,
        task=task,
        registry=registry,
        llm_router=llm_router,
        base_context={**base_context, "gap": gap},
        depth=depth,
        deadline=deadline,
        publisher=publisher,
        llm_override=llm_override,
    )
    if result.outcome != OUTCOME_OK:
        raise FeasibilityCheckError(
            f"feasibility-scout subagent 未正常完成（outcome={result.outcome}, "
            f"failures={result.tool_failures}: {result.tool_failure_reasons[:3]}）"
        )

    pack = _assemble_pack(gap_id, result.data or [])
    verdict = resolve_feasibility_verdict(pack, config)
    logger.info(
        "[feasibility_check] gap=%s verdict=%s (data=%s method=%s resource=%s)",
        gap_id, verdict["verdict"], verdict["data_status"],
        verdict["method_status"], verdict["resource_status"],
    )
    return {"gap_id": gap_id, "verdict": verdict, "pack": pack}
