# R/mod_ai_review.R — 综述写作 (每章一次 LLM 调用, 非流式)
#
# 注: 流式 SSE 推到 Phase 2.1 spike (req_perform_stream 已弃用,
# 新接口 req_perform_connection + resp_stream_sse 与 Shiny 异步集成需单独验证)

aiReviewUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_ai_review,
                "这页能做: AI 基于你的语料逐章生成一段文献综述初稿 (开题/课程作业可参考, 但务必自己核实事实和观点)"),
    analysis_card(
      "综述写作",
      desc = "选论型 + 字数档 → LLM 基于语料上下文 (build_context) 逐章生成中文综述. 可下载 .md.",
      fluidRow(
        column(6, selectInput(ns("type"), "论型",
                              choices = c("本科毕业"      = "undergrad",
                                          "硕士论文"      = "master",
                                          "博士论文"      = "phd",
                                          "国家基金"      = "grant",
                                          "博士开题"      = "proposal",
                                          "SCI Intro" = "sci_intro"))),
        # FINDING-005 修复: 删除暴露源码路径的提示, 改为面向用户的说明文案.
        column(6, helpText("不同论型对应不同章节结构与字数档; 单章默认 ≤ 4096 tokens."))
      ),
      fluidRow(
        column(3, actionButton(ns("go"),     "开始生成", status = "primary",
                                icon = icon("pen-fancy"))),
        column(3, actionButton(ns("cancel"), "取消",     status = "danger",
                                icon = icon("stop"))),
        column(3, downloadButton(ns("dl"), "下载 Markdown")),
        # FINDING-004 修复: 清空是 destructive 操作 (会丢失已生成章节),
        # 应当用 danger 而非 secondary, 与"取消"语义一致.
        column(3, actionButton(ns("clear"),  "清空", status = "danger",
                                icon = icon("trash")))
      ),
      br(),
      verbatimTextOutput(ns("status"))
    ),
    analysis_card("综述输出 (按章节展开)", uiOutput(ns("md")))
  )
}

aiReviewServer <- function(id, corpus, shared,
                           provider = "deepseek",
                           model = "deepseek-v4-flash") {
  moduleServer(id, function(input, output, session) {

    chapters <- reactiveVal(list())  # name = title, value = text
    # FINDING-014: 状态文案改为指向下一步动作
    status   <- reactiveVal("→ 选好论型后点击『开始生成』")
    cancel   <- reactiveVal(FALSE)
    tpl_used <- reactiveVal(NULL)

    observeEvent(input$go, {
      req(corpus())
      cancel(FALSE)
      chapters(list())
      tpl <- template_for(input$type)
      tpl_used(tpl)

      # build_context 依赖既有 Phase 1 函数
      ctx <- tryCatch(build_context(corpus()),
                      error = function(e) {
                        warning(sprintf("[降级] build_context: %s", safe_log_error(e)))
                        list(corpus_summary = list(n_docs = nrow(corpus())),
                             theme_clusters = data.frame(),
                             top_docs = data.frame(),
                             trend_topics = data.frame())
                      })

      n <- length(tpl$chapters)
      withProgress(message = sprintf("生成综述: %s", tpl$name), value = 0, {
        for (i in seq_len(n)) {
          if (cancel()) { status("已取消"); return(invisible()) }
          ch <- tpl$chapters[[i]]
          status(sprintf("生成中: %s (%d/%d)", ch$title, i, n))
          shiny::incProgress(1 / n, detail = ch$title)

          msg <- prompt_review(ctx, template = tpl, chapter = ch)
          # max_tokens 给得宽松一些, 因为 v4-flash 默认含思考模式
          r <- tryCatch(
            llm_call(provider, messages = msg, model = model,
                     max_tokens = 4096L,
                     api_key = resolve_llm_key(provider, session)),
            error = function(e) {
              warning(sprintf("[降级] 综述章节 %s: %s",
                               ch$title, safe_log_error(e)))
              list(text = sprintf("（生成失败: %s）", safe_log_error(e)),
                   usage = list(prompt_tokens_hit=0L,
                                prompt_tokens_miss=0L,
                                completion_tokens=0L))
            })
          shiny::isolate(cost_add(shared, provider, model, r$usage))
          chs <- chapters()
          chs[[ch$title]] <- r$text %||% ""
          chapters(chs)
        }
      })
      # v0.6: 章节同步到 shared, 供『导出报告』页打包
      shared$review_chapters <- chapters()
      status(sprintf("完成: %d 章", length(chapters())))
    })

    observeEvent(input$cancel, { cancel(TRUE); status("已取消") })
    observeEvent(input$clear,  { chapters(list()); status("已清空") })

    output$status <- renderText(status())

    output$md <- renderUI({
      chs <- chapters()
      if (!length(chs)) return(p("尚未生成. 选好论型后点击『开始生成』."))
      M <- tryCatch(corpus(), error = function(e) NULL)
      # I1 (spec §1.3 §1.5): 统一 markdown 渲染入口 (内置 sanitizer 防 XSS).
      # N2 (spec §6): 每章输出做引用完整性校验, 用 ✅⚠️❌ 标记真实/可疑/虚构引用,
      # 兑现"抗幻觉"承诺. annotated markdown 仍走 render_markdown_safe.
      do.call(tagList, lapply(names(chs), function(title) {
        txt <- chs[[title]]
        cc <- if (!is.null(M))
          tryCatch(check_citations(txt, M), error = function(e) NULL) else NULL
        body <- if (!is.null(cc)) cc$annotated else txt
        badge <- if (!is.null(cc)) {
          s <- cc$summary
          div(class = "biblio-cite-check", style = "margin:4px 0 8px; font-size:0.9em;",
              span(style = "color:#2e7d32;", sprintf("✅ 真实 %d  ", s$green %||% 0L)),
              span(style = "color:#ef6c00;", sprintf("⚠️ 待核 %d  ", s$yellow %||% 0L)),
              span(style = "color:#c62828;", sprintf("❌ 疑似虚构 %d", s$red %||% 0L)))
        } else NULL
        tagList(
          h3(title),
          badge,
          render_markdown_safe(body),
          tags$hr()
        )
      }))
    })

    output$dl <- downloadHandler(
      filename = function() {
        tpl <- tpl_used()
        sprintf("review_%s_%s.md",
                if (!is.null(tpl)) tpl$name else "review",
                format(Sys.time(), "%Y%m%d_%H%M%S"))
      },
      content = function(file) {
        chs <- chapters()
        md <- paste(vapply(names(chs),
                            function(t) sprintf("# %s\n\n%s\n", t, chs[[t]]),
                            character(1)),
                     collapse = "\n")
        writeLines(md, file, useBytes = TRUE)
      }
    )
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
