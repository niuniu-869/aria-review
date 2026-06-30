# R/fct_markdown.R — 全站统一 markdown 渲染入口
#
# 设计原则 (spec §1):
#   · 唯一公共入口: render_markdown_safe(md_text)
#   · LLM 输出必经此函数, 禁止直接 HTML() 渲染 LLM 文本
#   · 安全 first: commonmark 转 HTML 后, 经 xml2 + 白名单标签/属性清洗
#   · KISS: 标签白名单写死, 不开放配置, 减小 XSS 攻击面
#
# 安全约束:
#   · 拒绝 <script> <iframe> <object> <embed> <form> <input> 等执行/可交互标签
#   · 拒绝 on* 事件属性 (onclick / onerror / ...)
#   · href / src 仅允许 http / https / mailto, 拒绝 javascript:/data:
#   · style 属性整体剥除 (避免 CSS 注入)

# ---- 白名单 ----------------------------------------------------------------

SAFE_TAGS <- c(
  "h1","h2","h3","h4","h5","h6",
  "p","strong","em","del","s","u",
  "ul","ol","li",
  "a","code","pre","blockquote","hr","br",
  "table","thead","tbody","tr","th","td",
  "span","div","img"
)

# 每个标签允许的属性 (其余属性会被 xml2 set_attr(NULL) 移除)
SAFE_ATTRS <- list(
  a    = c("href", "title", "rel", "target"),
  img  = c("src", "alt", "title", "width", "height"),
  code = c("class"),   # 允许 language-* 给代码高亮用
  pre  = c("class"),
  span = c("class"),
  div  = c("class"),
  th   = c("align"),
  td   = c("align")
)

SAFE_URL_SCHEMES <- c("http", "https", "mailto", "")  # "" = 相对路径

# ---- 内部: URL 校验 --------------------------------------------------------

.is_safe_url <- function(url) {
  if (is.null(url) || is.na(url) || !nzchar(url)) return(TRUE)
  url <- tolower(trimws(url))
  if (startsWith(url, "#")) return(TRUE)         # 锚点
  if (startsWith(url, "/")) return(TRUE)         # 站内绝对路径
  scheme <- regmatches(url, regexpr("^[a-z][a-z0-9+.\\-]*:", url))
  if (length(scheme) == 0L) return(TRUE)         # 无 scheme = 相对路径
  sub(":$", "", scheme) %in% SAFE_URL_SCHEMES
}

# ---- 内部: 节点清洗 --------------------------------------------------------

.sanitize_node <- function(node) {
  tag <- tolower(xml2::xml_name(node))

  # 1. 标签不在白名单 -> 整节点连同 children 删除 (script/iframe 等)
  #    这是 strict 策略: 不试图保留 <script> 里的"文本", 因为这些标签
  #    出现在用户输入里 100% 是攻击向量 (markdown 正文用 ``` 写代码块).
  if (!tag %in% SAFE_TAGS) {
    xml2::xml_remove(node)
    return(invisible())
  }

  # 2. 属性清洗
  attrs <- xml2::xml_attrs(node)
  allow <- SAFE_ATTRS[[tag]] %||% character(0L)
  for (a in names(attrs)) {
    al <- tolower(a)
    keep <- al %in% allow && !startsWith(al, "on") && al != "style"
    if (keep && al %in% c("href", "src")) {
      if (!.is_safe_url(attrs[[a]])) keep <- FALSE
    }
    if (!keep) xml2::xml_attr(node, a) <- NULL
  }

  # 3. <a> 强制安全属性: rel="noopener noreferrer", target="_blank"
  if (tag == "a") {
    if (nzchar(xml2::xml_attr(node, "href") %||% "")) {
      xml2::xml_attr(node, "rel")    <- "noopener noreferrer"
      xml2::xml_attr(node, "target") <- "_blank"
    }
  }

  # 4. 递归处理 children (snapshot 拷贝, 避免删除时 iterator 失效)
  children <- xml2::xml_children(node)
  for (child in children) .sanitize_node(child)
}

# ---- 内部: sanitize_html ---------------------------------------------------

.sanitize_html <- function(html_str) {
  if (!nzchar(html_str)) return("")
  # commonmark 输出不带 root, 包一层以便 xml2 解析
  wrapped <- paste0("<div class='biblio-md'>", html_str, "</div>")
  doc <- tryCatch(
    xml2::read_html(wrapped, options = c("RECOVER", "NOERROR", "NOWARNING")),
    error = function(e) NULL)
  if (is.null(doc)) {
    # 解析失败: 退化为纯转义 <pre>, 保证不出 XSS
    return(paste0("<pre class='biblio-md-fallback'>",
                  htmltools::htmlEscape(html_str), "</pre>"))
  }
  body_div <- xml2::xml_find_first(doc, ".//div[@class='biblio-md']")
  if (inherits(body_div, "xml_missing")) {
    body_div <- xml2::xml_find_first(doc, ".//body")
  }
  if (inherits(body_div, "xml_missing")) return("")
  for (child in xml2::xml_children(body_div)) .sanitize_node(child)
  # 输出: as.character 会包 <!DOCTYPE>, 用 xml_contents 拼接子节点
  parts <- as.character(xml2::xml_contents(body_div))
  paste(parts, collapse = "")
}

# ---- 公共入口 --------------------------------------------------------------

#' 安全渲染 markdown 文本为 shiny::HTML
#'
#' 用 commonmark::markdown_html 转 HTML 后, 经 xml2 + 白名单清洗防 XSS.
#' LLM 输出场景必经此函数, 禁止直接 HTML().
#'
#' @param md_text character; markdown 原文 (可空, 可向量 — 向量时拼接)
#' @param fallback character; 输入为空时的占位文本 (markdown)
#' @return shiny::HTML
#' @export
render_markdown_safe <- function(md_text, fallback = "") {
  if (length(md_text) == 0L) md_text <- fallback
  if (length(md_text) > 1L)  md_text <- paste(md_text, collapse = "\n\n")
  if (is.na(md_text) || !nzchar(md_text)) md_text <- fallback
  if (!nzchar(md_text)) return(shiny::HTML(""))

  html <- tryCatch(
    commonmark::markdown_html(
      md_text,
      extensions = c("table", "strikethrough", "autolink"),
      smart = TRUE,
      sourcepos = FALSE),
    error = function(e) NULL)

  if (is.null(html)) {
    # commonmark 失败: 退化为纯转义 <pre>
    return(shiny::HTML(paste0("<pre class='biblio-md-fallback'>",
                              htmltools::htmlEscape(md_text), "</pre>")))
  }

  cleaned <- .sanitize_html(html)
  shiny::HTML(paste0("<div class='biblio-md'>", cleaned, "</div>"))
}

#' 渲染聊天气泡 (mod_ai_chat 专用)
#'
#' 比 render_markdown_safe 多一层 wrapper: 加角色标签 / 颜色.
#' 仍走同一个 sanitizer, 避免 XSS.
#'
#' @param role  "user" | "assistant"
#' @param content character; markdown 原文
#' @return shiny::tags$div
render_chat_bubble <- function(role, content) {
  is_user <- identical(role, "user")
  prefix  <- if (is_user) "你: "  else "AI: "
  color   <- if (is_user) "#1e88e5" else "#43a047"
  shiny::div(
    style = sprintf(
      "margin:8px 0; padding:10px; border-left:3px solid %s; background:#f5f5f5;",
      color),
    shiny::strong(prefix),
    render_markdown_safe(content)
  )
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
