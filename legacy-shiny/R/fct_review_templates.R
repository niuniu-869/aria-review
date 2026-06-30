# R/fct_review_templates.R — 综述论型模板 (精修, 内置抗幻觉硬约束)
#
# v0.6 (spec §8) 设计决议:
#   · Codex 二审主张 "模板数量不是能力, 3-5 个高质量即可, 内置 corpus 真实文献硬约束".
#   · 本项目已有 6 个 "论型" 模板 (本科/硕士/博士/基金/开题/SCI), 与产品
#     场景定位 (开题/基金/毕业论文) 强绑定, 已接线到 mod_ai_review UI.
#   · 决议: 保留 6 个场景论型 (优于 5 个抽象综述风格, 不丢场景锚定),
#     v0.6 的真正增量是给每个模板注入抗幻觉硬约束 + 章节写作指引.
#   · 这同时满足 spec §8 的真实意图 (curated 高质量 + corpus grounding).
#
# 每种返回 list(name, tone, chapters, guidance).
# chapters 为 list of list(title, word_budget, focus).
# guidance 为该论型的整体写作指引 (注入 system prompt).

#' 抗幻觉硬约束 (spec §8.2 + N2 引用校验的 prompt 层前置防线)
#'
#' 所有论型共用. prompt_review 会把它拼进 system 提示, 要求 LLM:
#'   1. 每个论点/数据/结论必须来自 context 提供的 top_docs 真实文献
#'   2. 引用标号 [n] 必须对应 top_docs 的真实行号 (从 1 起)
#'   3. 严禁编造文献、作者、年份、DOI
#'   4. context 不足以支撑某论点时, 明确写 "(语料未覆盖, 需补充检索)"
#' v0.6 仅在 prompt 层约束; 程序级校验由 N2 check_citations() 在输出后兜底.
REVIEW_GROUNDING_DIRECTIVE <- paste0(
  "【抗幻觉硬约束】(1) 每个论点、数据、结论都必须来自 <context> 提供的 top_docs 真实文献; ",
  "(2) 引用标号用 [n], n 为 top_docs 行号 (从 1 起), 必须真实对应, 不得编号到不存在的文献; ",
  "(3) 严禁编造文献、作者、年份、期刊或 DOI; ",
  "(4) 若 context 不足以支撑某个论点, 明确写出 \"(语料未覆盖, 需补充检索)\", 不要凭空补全."
)

template_for <- function(type) {
  switch(type,
    undergrad = list(
      name = "本科毕业论文综述", tone = "规范",
      guidance = "面向本科生, 语言通俗清晰, 重点是梳理清楚研究脉络, 不必追求理论深度.",
      chapters = list(
        list(title = "研究背景与意义", word_budget = 600L,
             focus = "交代研究主题的现实/学术背景, 说明为什么值得研究."),
        list(title = "国内外研究现状", word_budget = 1200L,
             focus = "按主题或时间顺序梳理已有研究, 区分国内外, 标注代表性文献 [n]."),
        list(title = "研究述评与展望", word_budget = 600L,
             focus = "总结已有研究的贡献与不足, 指出尚未解决的问题."))),
    master = list(
      name = "硕士论文综述", tone = "学术",
      guidance = "面向硕士生, 需体现一定的批判性, 国内外分述, 突出研究空白.",
      chapters = list(
        list(title = "研究背景与问题",       word_budget = 800L,
             focus = "从背景引出明确的研究问题, 论证问题的重要性."),
        list(title = "国外研究综述",         word_budget = 1500L,
             focus = "梳理国外研究的理论框架、方法与结论, 标注代表文献 [n]."),
        list(title = "国内研究综述",         word_budget = 1500L,
             focus = "梳理国内研究进展, 与国外对比, 标注代表文献 [n]."),
        list(title = "文献述评与研究空白",   word_budget = 800L,
             focus = "批判性总结, 明确指出本研究要填补的空白."))),
    phd = list(
      name = "博士论文综述", tone = "深入学术",
      guidance = "面向博士生, 需有理论深度与方法学反思, 体现研究脉络的演化与聚类.",
      chapters = list(
        list(title = "研究背景与理论基础",         word_budget = 1200L,
             focus = "梳理核心理论与概念演化, 建立分析框架."),
        list(title = "国外研究脉络与代表性成果",   word_budget = 2500L,
             focus = "按学派/方法/时间梳理国外研究脉络, 深入评析代表成果 [n]."),
        list(title = "国内研究脉络与代表性成果",   word_budget = 2500L,
             focus = "梳理国内研究脉络, 与国际前沿对照 [n]."),
        list(title = "主题聚类与方法学进展",       word_budget = 1500L,
             focus = "结合 context 的 theme_clusters, 归纳研究主题群与方法学演进."),
        list(title = "研究空白与本研究定位",       word_budget = 1500L,
             focus = "在已有脉络中精确定位本研究的理论与方法贡献."))),
    grant = list(
      name = "国家基金本子综述", tone = "精炼",
      guidance = "面向基金申请书, 语言精炼有力, 突出紧迫性与关键科学问题.",
      chapters = list(
        list(title = "研究意义与紧迫性",     word_budget = 400L,
             focus = "用最精炼的语言论证选题的科学价值与现实紧迫性."),
        list(title = "国内外研究进展",       word_budget = 1500L,
             focus = "高密度概述国内外进展, 突出前沿与争议 [n]."),
        list(title = "尚需解决的关键问题",   word_budget = 400L,
             focus = "凝练出 1-3 个关键科学问题, 作为本子的切入点."))),
    proposal = list(
      name = "博士开题报告综述", tone = "学术",
      guidance = "面向开题答辩, 需说服评委选题可行且有价值, 研究空白要清晰.",
      chapters = list(
        list(title = "选题背景",                   word_budget = 600L,
             focus = "交代选题的来龙去脉与学术/现实价值."),
        list(title = "国内外研究现状",             word_budget = 1800L,
             focus = "系统梳理研究现状, 标注代表文献 [n], 为研究空白做铺垫."),
        list(title = "主要研究空白与本研究价值",   word_budget = 600L,
             focus = "明确研究空白, 论证本研究的填补价值与可行性."))),
    sci_intro = list(
      name = "SCI 论文 Introduction", tone = "academic English",
      guidance = "Write in concise academic English. Follow the funnel structure: broad context to specific gap to contribution.",
      chapters = list(
        list(title = "Background and motivation", word_budget = 350L,
             focus = "Establish the broad research context and why it matters, cite key works [n]."),
        list(title = "Literature gap",            word_budget = 350L,
             focus = "Narrow down to the specific unresolved gap, supported by [n]."),
        list(title = "Contribution and structure", word_budget = 200L,
             focus = "State the contribution and outline the paper structure."))),
    stop(sprintf("未知论型: %s", type))
  )
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
