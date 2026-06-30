"""P3-4 — grounding / 质量指标 harness（纯函数，无 I/O，无 DB 依赖）。

对 build_runlog 产出的 RunLog（schema=runlog/v1）计算三大 grounding 指标：

指标定义
--------
不可评分约定（codex P1，竞赛诚信）
    若整份 run 一条引用都没有（green+yellow+fabricated==0，即「空 review/空 RunLog」），
    三大率一律返回 **None**（而非 1.0 满分），并置 insufficient_evidence=True /
    scoreable=False。报告/CLI 须把该 case 标为「不可评分」——空 review 不得在可信度
    评分表里伪装成 100%（那是可被 gaming 的报告风险）。

grounding_accuracy
    = (green + yellow) / (green + yellow + fabricated_count)
    * fabricated_count 取自 manifest.fabricated_count（来源 validation_summary，
      不从 evidence_refs 推算——红色引用从不进 evidence_refs）。
    * 无任何引用 → None（不可评分）。

provenance_hit_rate
    = (source_content_sha256 命中 corpus_hashes 的 evidence 条目数) / (evidence 总数)
    * corpus_hashes=None → 返回 None（无语料哈希时无法判定）。
    * evidence 总数为 0 → None（不可评分，不伪装满分）。
    * source_content_sha256 为 None 或缺失 → 该条算作未命中。

zero_fabrication_rate
    = 1 - fabricated_count / 总引用数，总引用 = green + yellow + fabricated_count
    * 无任何引用 → None（不可评分）。

附加字段（便于报告）
    insufficient_evidence: bool / scoreable: bool（无引用时不可评分）
    evidence_count / fabricated_count / green_count / yellow_count
"""
from __future__ import annotations

import random
from typing import Any


def grounding_metrics(
    runlog: dict,
    corpus_hashes: set[str] | None = None,
) -> dict[str, Any]:
    """从 RunLog dict 计算 grounding 质量指标。

    参数
    ----
    runlog:
        build_runlog 产出的 RunLog dict（schema=runlog/v1）。
        允许 manifest/evidence_refs 缺失或为空，函数会容错处理。
    corpus_hashes:
        语料文档内容哈希集合（source_content_sha256 值域），由调用方从
        语料库或 --corpus-hashes 文件构建。传 None 时 provenance_hit_rate
        返回 None（无法判定溯源命中）。

    返回
    ----
    包含以下键的 dict：
        grounding_accuracy   : float（[0,1]；分母 0 → 1.0）
        provenance_hit_rate  : float | None（corpus_hashes=None → None；分母 0 → 1.0）
        zero_fabrication_rate: float（[0,1]；分母 0 → 1.0）
        evidence_count       : int
        fabricated_count     : int
        green_count          : int
        yellow_count         : int
    """
    # ---- 1. 读取 evidence_refs ----
    evidence_refs: list[dict] = runlog.get("evidence_refs") or []

    # 统计 green / yellow 数量（只计 match_quality 为 green/yellow 的条目）
    green_count = sum(1 for e in evidence_refs if e.get("match_quality") == "green")
    yellow_count = sum(1 for e in evidence_refs if e.get("match_quality") == "yellow")
    evidence_count = len(evidence_refs)  # evidence_refs 仅含 green/yellow

    # ---- 2. fabricated_count 取自 manifest（不从 evidence_refs 推）----
    manifest: dict = runlog.get("manifest") or {}
    fabricated_count: int = int(manifest.get("fabricated_count") or 0)

    # ---- 3. 无引用（空 review/空 RunLog）→ 不可评分（codex P1）----
    # 关键诚信约定：若整份 run 一条引用都没有（green+yellow+fabricated==0），
    # 各率**返回 None**（而非 1.0 满分），并置 insufficient_evidence=True。
    # 否则空 review 会在可信度评分表里伪装成 100%，是可被 gaming 的报告风险。
    # 报告/CLI 应把该 case 标为「不可评分」，而非完美。
    cited_total = green_count + yellow_count + fabricated_count
    insufficient_evidence = cited_total == 0

    # ---- 4. grounding_accuracy ----
    grounding_accuracy: float | None
    if insufficient_evidence:
        grounding_accuracy = None
    else:
        grounding_accuracy = (green_count + yellow_count) / cited_total

    # ---- 5. provenance_hit_rate ----
    provenance_hit_rate: float | None
    if corpus_hashes is None:
        provenance_hit_rate = None  # 无语料哈希：无法判定溯源
    elif evidence_count == 0:
        provenance_hit_rate = None  # 无 evidence：不可评分（不伪装满分）
    else:
        hit_count = sum(
            1
            for e in evidence_refs
            if e.get("source_content_sha256") and
               e["source_content_sha256"] in corpus_hashes
        )
        provenance_hit_rate = hit_count / evidence_count

    # ---- 6. zero_fabrication_rate ----
    zero_fabrication_rate: float | None
    if insufficient_evidence:
        zero_fabrication_rate = None
    else:
        zero_fabrication_rate = 1.0 - fabricated_count / cited_total

    return {
        "grounding_accuracy": grounding_accuracy,
        "provenance_hit_rate": provenance_hit_rate,
        "zero_fabrication_rate": zero_fabrication_rate,
        # scoreable=False 时上面三率为 None，报告须标「不可评分」而非满分
        "insufficient_evidence": insufficient_evidence,
        "scoreable": not insufficient_evidence,
        "evidence_count": evidence_count,
        "fabricated_count": fabricated_count,
        "green_count": green_count,
        "yellow_count": yellow_count,
    }


def parse_fidelity_spotcheck(
    papers: list[dict],
    sample_size: int | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """MinerU 解析质量抽检：计算论文字段非空率。

    对传入论文列表（由调用方从 DB/语料填充）随机抽样，计算：
      - title_nonempty_rate     : title 字段非空比例
      - abstract_nonempty_rate  : abstract 字段非空比例
      - body_length_gt0_rate    : body 字段长度 > 0 比例

    参数
    ----
    papers:
        论文 dict 列表，每条应含 title / abstract / body 键（缺失按空处理）。
    sample_size:
        抽样数量（None 或 <= 0 时使用全量）。抽样多于列表总量时用全量。
    seed:
        随机种子（可重现测试用）。

    返回
    ----
    dict，包含：
        title_nonempty_rate     : float | None（列表空 → None）
        abstract_nonempty_rate  : float | None
        body_length_gt0_rate    : float | None
        sample_size             : int（实际参与统计的论文数）
    """
    if not papers:
        return {
            "title_nonempty_rate": None,
            "abstract_nonempty_rate": None,
            "body_length_gt0_rate": None,
            "sample_size": 0,
        }

    # 抽样
    pool = list(papers)
    if sample_size and 0 < sample_size < len(pool):
        rng = random.Random(seed)
        pool = rng.sample(pool, sample_size)

    n = len(pool)

    def _nonempty(val: Any) -> bool:
        """判定字段非空（None / "" / 全空白 均视为空）。"""
        return bool(val and str(val).strip())

    title_hits = sum(1 for p in pool if _nonempty(p.get("title")))
    abstract_hits = sum(1 for p in pool if _nonempty(p.get("abstract")))
    body_hits = sum(1 for p in pool if _nonempty(p.get("body")))

    return {
        "title_nonempty_rate": title_hits / n,
        "abstract_nonempty_rate": abstract_hits / n,
        "body_length_gt0_rate": body_hits / n,
        "sample_size": n,
    }
