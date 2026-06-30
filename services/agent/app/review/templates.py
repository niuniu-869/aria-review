"""综述论型模板 — 移植自 legacy-shiny/R/fct_review_templates.R (v0.6 精修版)

设计决议（codex P1，见 spec §5）：
  - 单遍模板注入：在 generate_review() 的 system prompt 末尾一次性注入
    "章节大纲 + guidance + REVIEW_GROUNDING_DIRECTIVE"。
  - 绝不逐章 reduce，绝不逐章 GuardedStream。
  - 保留单条 GuardedStream 校验链，review_complete/error 语义不改。

6 论型场景锚定（与产品场景强绑定）：
  undergrad / master / phd / grant / proposal / sci_intro
"""
from __future__ import annotations

from dataclasses import dataclass


# ============================================================
# 抗幻觉硬约束（所有论型共用，注入 system prompt 末尾）
# ============================================================

REVIEW_GROUNDING_DIRECTIVE = (
    "【抗幻觉硬约束】(1) 每个论点、数据、结论都必须来自工具实际返回的真实文献; "
    "(2) 引用标号用 [n], n 为语料文献的真实行号(从1起), 必须真实对应, 不得编号到不存在的文献; "
    "(3) 严禁编造文献、作者、年份、期刊或 DOI; "
    "(4) 若语料不足以支撑某个论点, 明确写出 \"(语料未覆盖, 需补充检索)\", 不要凭空补全。"
)


# ============================================================
# 数据模型（frozen dataclass，不可变）
# ============================================================

@dataclass(frozen=True)
class Chapter:
    """综述章节描述符。"""
    title: str
    word_budget: int
    focus: str


@dataclass(frozen=True)
class Template:
    """综述论型模板。"""
    key: str
    name: str
    tone: str
    guidance: str
    chapters: tuple[Chapter, ...]  # 用 tuple 保持 frozen 不可变性


# ============================================================
# 6 论型模板（照搬 fct_review_templates.R: 32-100）
# ============================================================

PAPER_TYPE_TEMPLATES: dict[str, Template] = {
    "undergrad": Template(
        key="undergrad",
        name="本科毕业论文综述",
        tone="规范",
        guidance="面向本科生, 语言通俗清晰, 重点是梳理清楚研究脉络, 不必追求理论深度.",
        chapters=(
            Chapter(
                title="研究背景与意义",
                word_budget=600,
                focus="交代研究主题的现实/学术背景, 说明为什么值得研究.",
            ),
            Chapter(
                title="国内外研究现状",
                word_budget=1200,
                focus="按主题或时间顺序梳理已有研究, 区分国内外, 标注代表性文献 [n].",
            ),
            Chapter(
                title="研究述评与展望",
                word_budget=600,
                focus="总结已有研究的贡献与不足, 指出尚未解决的问题.",
            ),
        ),
    ),
    "master": Template(
        key="master",
        name="硕士论文综述",
        tone="学术",
        guidance="面向硕士生, 需体现一定的批判性, 国内外分述, 突出研究空白.",
        chapters=(
            Chapter(
                title="研究背景与问题",
                word_budget=800,
                focus="从背景引出明确的研究问题, 论证问题的重要性.",
            ),
            Chapter(
                title="国外研究综述",
                word_budget=1500,
                focus="梳理国外研究的理论框架、方法与结论, 标注代表文献 [n].",
            ),
            Chapter(
                title="国内研究综述",
                word_budget=1500,
                focus="梳理国内研究进展, 与国外对比, 标注代表文献 [n].",
            ),
            Chapter(
                title="文献述评与研究空白",
                word_budget=800,
                focus="批判性总结, 明确指出本研究要填补的空白.",
            ),
        ),
    ),
    "phd": Template(
        key="phd",
        name="博士论文综述",
        tone="深入学术",
        guidance="面向博士生, 需有理论深度与方法学反思, 体现研究脉络的演化与聚类.",
        chapters=(
            Chapter(
                title="研究背景与理论基础",
                word_budget=1200,
                focus="梳理核心理论与概念演化, 建立分析框架.",
            ),
            Chapter(
                title="国外研究脉络与代表性成果",
                word_budget=2500,
                focus="按学派/方法/时间梳理国外研究脉络, 深入评析代表成果 [n].",
            ),
            Chapter(
                title="国内研究脉络与代表性成果",
                word_budget=2500,
                focus="梳理国内研究脉络, 与国际前沿对照 [n].",
            ),
            Chapter(
                title="主题聚类与方法学进展",
                word_budget=1500,
                focus="结合 context 的 theme_clusters, 归纳研究主题群与方法学演进.",
            ),
            Chapter(
                title="研究空白与本研究定位",
                word_budget=1500,
                focus="在已有脉络中精确定位本研究的理论与方法贡献.",
            ),
        ),
    ),
    "grant": Template(
        key="grant",
        name="国家基金本子综述",
        tone="精炼",
        guidance="面向基金申请书, 语言精炼有力, 突出紧迫性与关键科学问题.",
        chapters=(
            Chapter(
                title="研究意义与紧迫性",
                word_budget=400,
                focus="用最精炼的语言论证选题的科学价值与现实紧迫性.",
            ),
            Chapter(
                title="国内外研究进展",
                word_budget=1500,
                focus="高密度概述国内外进展, 突出前沿与争议 [n].",
            ),
            Chapter(
                title="尚需解决的关键问题",
                word_budget=400,
                focus="凝练出 1-3 个关键科学问题, 作为本子的切入点.",
            ),
        ),
    ),
    "proposal": Template(
        key="proposal",
        name="博士开题报告综述",
        tone="学术",
        guidance="面向开题答辩, 需说服评委选题可行且有价值, 研究空白要清晰.",
        chapters=(
            Chapter(
                title="选题背景",
                word_budget=600,
                focus="交代选题的来龙去脉与学术/现实价值.",
            ),
            Chapter(
                title="国内外研究现状",
                word_budget=1800,
                focus="系统梳理研究现状, 标注代表文献 [n], 为研究空白做铺垫.",
            ),
            Chapter(
                title="主要研究空白与本研究价值",
                word_budget=600,
                focus="明确研究空白, 论证本研究的填补价值与可行性.",
            ),
        ),
    ),
    "sci_intro": Template(
        key="sci_intro",
        name="SCI 论文 Introduction",
        tone="academic English",
        guidance=(
            "Write in concise academic English. "
            "Follow the funnel structure: broad context to specific gap to contribution."
        ),
        chapters=(
            Chapter(
                title="Background and motivation",
                word_budget=350,
                focus="Establish the broad research context and why it matters, cite key works [n].",
            ),
            Chapter(
                title="Literature gap",
                word_budget=350,
                focus="Narrow down to the specific unresolved gap, supported by [n].",
            ),
            Chapter(
                title="Contribution and structure",
                word_budget=200,
                focus="State the contribution and outline the paper structure.",
            ),
        ),
    ),
}


# ============================================================
# 公开 API
# ============================================================

def get_template(paper_type: str | None) -> Template | None:
    """按论型 key 取模板，未知或 None 返回 None。"""
    return PAPER_TYPE_TEMPLATES.get(paper_type or "")
