# ui_helpers.R — 中文 UI 复用组件
#
# 注: `%||%` 由 R/fct_cite.R / fct_cost.R / fct_crossref.R 等先于本文件 source
# 的工具层定义为 `if (is.null(a) || length(a) == 0L) b else a` —— 复用既有定义,
# 不在此处重新定义以避免覆盖语义.

#' 校验语料是否有效（可复用于上传校验与单测）
#'
#' @param M 待校验的语料对象
#' @return TRUE 表示有效，FALSE 表示无效
valid_corpus <- function(M) {
  is.data.frame(M) &&
    nrow(M) > 0 &&
    all(c("AU", "TI", "PY") %in% names(M))
}

#' 数据未就绪时的中文占位提示
empty_hint <- function() {
  div(style = "padding:40px; text-align:center; color:#888;",
      icon("circle-info"), span(LBL$no_data))
}

#' 模块顶部 page header (FINDING-003).
#'
#' BiblioCN 全站原先只有 card-header (H3) 而无 H1, 导致 42 个 H3 字号全等,
#' 用户切换 tab 后无视觉锚定 "我现在在 X 页". 在每个模块 UI 顶部注入
#' H1 + 可选 subtitle 形成正确的层级: H1 > card 头 (H4/600).
#'
#' @param title    页面主标题 (中文短语, 例: "数据导入")
#' @param subtitle 可选副标题 (中文长句, 一句话说明本页能做什么)
page_header <- function(title, subtitle = NULL) {
  div(class = "biblio-page-header",
      h1(class = "biblio-page-title", title),
      if (!is.null(subtitle))
        p(class = "biblio-page-subtitle", subtitle))
}

#' 带中文标题与说明的分析卡片
#'
#' FINDING-006 修复: 原先 solidHeader=TRUE + status=primary 让每张卡片都套上
#' #007bff 大蓝条头, 全站视觉噪音过强, 真正的 CTA 蓝色按钮被稀释.
#' 现改为 solidHeader=FALSE + status=primary, bs4Dash 自动渲染为白底卡片头 +
#' 左侧细蓝条 (具体样式见 www/biblio.css), 把视觉焦点交还给内容与 CTA.
#'
#' class = "analysis-card" 让 CSS 仅作用于本组件渲染的卡片, 避免影响未来
#' 直接调 bs4Dash::box(status='primary') 的其他用法 (codex P0 review).
analysis_card <- function(title, ..., desc = NULL) {
  # bs4Dash::box 返回外层栅格 col 包着内层 .card 实际元素;
  # analysis-card class 必须打到内层 .card 才能匹配 CSS 选择器.
  card_wrapper <- bs4Dash::box(
    title = title, width = 12, status = "primary",
    solidHeader = FALSE, collapsible = TRUE,
    if (!is.null(desc)) p(style = "color:#666;", desc),
    ...
  )
  card_wrapper$children[[1]] <- htmltools::tagAppendAttributes(
    card_wrapper$children[[1]], class = "analysis-card")
  card_wrapper
}
