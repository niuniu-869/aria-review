# mod_welcome.R — 新人零门槛入口页
#
# 取代过去"默认登陆数据导入页 → 空状态"的入口模式. 用户首次打开应用,
# 看到的是 4 张并列入口卡片, 对应 4 种"我现在的状态":
#   A. 我有一个研究主题      → OpenAlex 主题词搜索
#   B. 我有一份参考文献清单  → DeepSeek 解析 + OpenAlex 反查
#   D. 我什么都没有          → 加载演示数据
#   E. 我已经有 WoS/Scopus   → 跳上传页
#
# API 状态灯: 启动时探测 OpenAlex / Crossref / DeepSeek, 灰显不可用卡片.
#
# 跨模块状态: 通过外部传入的 corpus_rv (reactiveVal) 向主 app 输出 corpus,
# 与 mod_upload.R 共用同一个 reactiveVal — 谁写谁更新, 下游模块统一消费.

welcomeUI <- function(id) {
  ns <- NS(id)
  tagList(
    # Hero — 一行讲清楚价值, 学生看得懂的话
    div(class = "biblio-welcome-hero",
        h1("BiblioCN  文献综述的零门槛助手"),
        p(class = "biblio-welcome-tagline",
          "写开题报告? 写文献综述? 找一个研究方向的核心论文?",
          br(),
          tags$strong("输入一个主题词, 或粘几篇参考文献, AI 10 秒帮你拉出领域全景图."))),

    # 探测中提示 — server 端探完会更新这里
    uiOutput(ns("probe_status_banner")),

    # 第一行: 路径 A 主题词搜索 + 路径 D 演示数据 (最大杠杆双卡)
    fluidRow(
      column(7,
        analysis_card(
          uiOutput(ns("a_title"), inline = TRUE),
          desc = paste(
            "比如「IPO 招股书 文本分析」「双碳 企业绿色创新」, 我们会从",
            "OpenAlex (覆盖 2 亿+ 论文的开放学术数据库) 自动检索相关文献,",
            "10 秒内拼出可分析的语料."),
          textInput(ns("topic"), label = NULL,
                    placeholder = "粘贴或输入你的研究主题词...",
                    width = "100%"),
          fluidRow(
            column(4,
              selectInput(ns("topic_n"), "篇数",
                          choices = c("20" = 20, "50" = 50, "100" = 100),
                          selected = 50)),
            column(4,
              selectInput(ns("topic_since"), "起始年份",
                          choices = c("近 5 年" = "2021-01-01",
                                      "近 10 年" = "2016-01-01",
                                      "近 20 年" = "2006-01-01"),
                          selected = "2016-01-01")),
            column(4, br(),
              actionButton(ns("topic_go"), "开始检索",
                           icon = icon("magnifying-glass"),
                           status = "primary"))
          ),
          br(),
          uiOutput(ns("topic_log"))
        )
      ),
      column(5,
        analysis_card(
          uiOutput(ns("d_title"), inline = TRUE),
          desc = paste(
            "完全不知道从哪开始? 我们内置了 50 篇 IPO/招股书 × 文本分析",
            "主题的真实文献, 立刻体验全部 8 项分析 + AI 助手."),
          actionButton(ns("demo_go"),
                       "加载演示数据 (50 篇)",
                       icon = icon("play-circle"),
                       status = "success", width = "100%"),
          br(), br(),
          uiOutput(ns("demo_log"))
        )
      )
    ),

    # 第二行: 路径 B 粘贴 + 路径 E 上传
    fluidRow(
      column(7,
        analysis_card(
          uiOutput(ns("b_title"), inline = TRUE),
          desc = paste(
            "导师给了你一份参考文献清单? 从 Google Scholar 复制了几条结果?",
            "AI 会解析出标题/作者/年份, 再用 OpenAlex 自动补全摘要、",
            "关键词、引用文献等所有元数据."),
          textAreaInput(ns("refs_text"), label = NULL,
                        rows = 4, width = "100%",
                        placeholder = paste(
                          "示例:",
                          "Loughran, T., & McDonald, B. (2016). Textual analysis in accounting...",
                          "Hanley, K. W., & Hoberg, G. (2010). The information content of IPO prospectuses...",
                          sep = "\n")),
          actionButton(ns("refs_go"),
                       "AI 智能解析",
                       icon = icon("wand-magic-sparkles"),
                       status = "primary"),
          br(), br(),
          uiOutput(ns("refs_log"))
        )
      ),
      column(5,
        analysis_card(
          tags$span(icon("upload"), " 我已经有 WoS/Scopus 文件"),
          desc = paste(
            "已经从 Web of Science 或 Scopus 导出了 .txt/.bib/.csv? 直接跳",
            "「数据导入」页上传."),
          actionButton(ns("upload_go"),
                       "去上传页",
                       icon = icon("arrow-right"),
                       status = "secondary", width = "100%")
        )
      )
    )
  )
}

#' Welcome 模块 server
#'
#' @param id          模块 id
#' @param corpus_rv   reactiveVal(corpus) — 共享 corpus 出口, 与 mod_upload 共用
#' @param parent      调用方 session, 用于 updateTabItems
welcomeServer <- function(id, corpus_rv, parent) {
  moduleServer(id, function(input, output, session) {

    # ---- API 探测 (启动一次, 用户可手动重试) ----
    api_rv <- reactiveVal(NULL)
    observe({
      api_rv(probe_apis())
    })

    # ---- 卡片标题 + 状态灯 ----
    .titled <- function(icon_name, text, badge_color, badge_text) {
      tags$span(
        icon(icon_name), " ", text,
        tags$span(style = sprintf("float:right;font-size:0.85em;color:%s;",
                                    badge_color),
                   badge_text)
      )
    }

    output$a_title <- renderUI({
      probe <- api_rv()
      if (is.null(probe)) {
        .titled("bullseye", "用一个主题词开始", "#999", "● 探测中...")
      } else {
        ok <- api_card_enabled(probe$openalex)
        .titled("bullseye", "用一个主题词开始",
                if (ok) "#28a745" else "#dc3545",
                api_status_badge(probe$openalex))
      }
    })
    output$d_title <- renderUI({
      .titled("gift", "先看看效果", "#28a745", "● 离线可用")
    })
    output$b_title <- renderUI({
      probe <- api_rv()
      if (is.null(probe)) {
        .titled("paste", "我有一份参考文献清单", "#999", "● 探测中...")
      } else {
        ok <- api_card_enabled(probe$deepseek) && api_card_enabled(probe$openalex)
        msg <- if (!api_card_enabled(probe$deepseek))
                 "● 需要配置 AI key (设置页)"
               else if (!api_card_enabled(probe$openalex))
                 "● 网络不通"
               else "● 已配置"
        .titled("paste", "我有一份参考文献清单",
                if (ok) "#28a745" else "#dc3545", msg)
      }
    })

    output$probe_status_banner <- renderUI({
      probe <- api_rv()
      if (is.null(probe)) return(NULL)
      if (!api_card_enabled(probe$openalex)) {
        div(class = "alert alert-warning", style = "margin-bottom:15px;",
            icon("triangle-exclamation"),
            " 检测不到 OpenAlex 学术数据库连接, 路径 A/B 暂时不可用. ",
            "你仍然可以使用「演示数据」或「上传 WoS/Scopus 文件」.")
      }
    })

    # ---- 操作 1: 加载演示数据 ----
    observeEvent(input$demo_go, {
      output$demo_log <- renderUI(div("加载中...", class = "text-muted"))
      withProgress(message = "加载演示数据...", value = 0.5, {
        M <- load_demo_corpus()
      })
      if (is.null(M)) {
        output$demo_log <- renderUI(
          div(class = "text-danger", "演示数据加载失败. 请检查 data/demo/ 目录."))
        return()
      }
      corpus_rv(M)
      output$demo_log <- renderUI(
        div(class = "text-success",
            sprintf("已加载 %d 条文献 — 跳转到「概览」查看!", nrow(M))))
      bs4Dash::updateTabItems(parent, "menu", "overview")
    })

    # ---- 操作 2: 主题词搜索 (路径 A) ----
    topic_log_rv <- reactiveVal(character(0))
    observeEvent(input$topic_go, {
      req(nzchar(input$topic))
      topic_log_rv(character(0))
      output$topic_log <- renderUI(div("准备开始...", class = "text-muted"))
      n     <- as.integer(input$topic_n)
      since <- input$topic_since
      withProgress(message = sprintf("OpenAlex 检索「%s」", input$topic),
                   value = 0, {
        progress_cb <- function(stage, done, total, msg) {
          frac <- if (!is.null(total) && total > 0) min(1, done / total) else 0
          setProgress(value = frac, detail = msg)
          topic_log_rv(c(topic_log_rv(),
                          sprintf("[%s] %s", stage, msg)))
        }
        M <- tryCatch(
          oa_corpus_from_topic(input$topic, n = n, since = since,
                                with_refs = TRUE,
                                on_progress = progress_cb),
          error = function(e) {
            output$topic_log <<- renderUI(
              div(class = "text-danger",
                  sprintf("检索失败: %s", conditionMessage(e))))
            NULL
          })
      })
      if (is.null(M) || nrow(M) == 0) {
        output$topic_log <- renderUI(div(class = "text-warning",
          "未找到结果, 试试调整主题词? 比如换成英文."))
        return()
      }
      corpus_rv(M)
      output$topic_log <- renderUI(
        div(class = "text-success",
            sprintf("已检索 %d 条文献, 跳转到「概览」查看分析结果.", nrow(M))))
      bs4Dash::updateTabItems(parent, "menu", "overview")
    })

    # ---- 操作 3: AI 解析非结构化 (路径 B) ----
    observeEvent(input$refs_go, {
      req(nzchar(input$refs_text))
      output$refs_log <- renderUI(div("AI 正在解析...", class = "text-muted"))
      withProgress(message = "AI 解析中...", value = 0, {
        progress_cb <- function(stage, done, total, msg) {
          frac <- if (!is.null(total) && total > 0) min(1, done / total) else 0
          setProgress(value = frac, detail = msg)
        }
        M <- tryCatch(
          parse_refs_to_corpus(input$refs_text, with_refs = TRUE,
                                 on_progress = progress_cb,
                                 api_key = resolve_llm_key("deepseek", session)),
          error = function(e) {
            output$refs_log <<- renderUI(
              div(class = "text-danger",
                  sprintf("解析失败: %s", safe_log_error(e))))
            NULL
          })
      })
      if (is.null(M) || nrow(M) == 0) {
        output$refs_log <- renderUI(div(class = "text-warning",
          "未识别出有效文献条目. 试试加上 DOI 或者补全标题."))
        return()
      }
      unmatched <- attr(M, "unmatched")
      um_n <- if (is.null(unmatched)) 0 else length(unmatched)
      corpus_rv(M)
      output$refs_log <- renderUI(
        div(class = "text-success",
            sprintf("已构建 %d 条语料%s — 跳转到「概览」查看.",
                    nrow(M),
                    if (um_n) sprintf(" (跳过 %d 条无法匹配)", um_n) else "")))
      bs4Dash::updateTabItems(parent, "menu", "overview")
    })

    # ---- 操作 4: 跳转上传页 ----
    observeEvent(input$upload_go, {
      bs4Dash::updateTabItems(parent, "menu", "upload")
    })
  })
}
