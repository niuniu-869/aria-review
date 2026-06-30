# R/mod_ai_rewrite.R — 交互重写 (4 个动作)

aiRewriteUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_ai_rewrite,
                "这页能做: 粘一段你写的文字, AI 帮你润色/改写/换语气 (口语→学术 / 英→中 / 长→短等, 不依赖语料)"),
    analysis_card(
      "交互重写",
      desc = "粘贴一段文字 → 选动作 → LLM 重写. 不依赖语料 (任意文本均可).",
      textAreaInput(ns("text"), "原文段落 (≤ 3000 字)",
                    rows = 8, placeholder = "粘贴一段中文/英文文字……"),
      fluidRow(
        column(6, selectInput(ns("action"), "重写动作",
                              choices = c("变论点 (反驳)"       = "counter",
                                          "压缩到 60%"          = "compress",
                                          "扩写到 150%"         = "expand",
                                          "粉剧本化 (短视频脚本)" = "casual"))),
        column(6, actionButton(ns("go"), "重写",
                                status = "primary", icon = icon("wand-magic-sparkles")))
      ),
      br(),
      verbatimTextOutput(ns("status"))
    ),
    fluidRow(
      # I1 (spec §1.5): 输出改为统一 markdown 渲染, 既能展示 AI 输出的
      # 标题/列表/粗体, 又走 sanitizer 防 XSS.
      column(6, analysis_card("原文",   uiOutput(ns("orig")))),
      column(6, analysis_card("重写后", uiOutput(ns("out"))))
    )
  )
}

aiRewriteServer <- function(id, shared,
                            provider = "deepseek",
                            model = "deepseek-v4-flash") {
  moduleServer(id, function(input, output, session) {
    out_text <- reactiveVal("")
    # FINDING-014: 状态文案改为指向下一步动作
    status   <- reactiveVal("→ 粘贴文字后选动作并点击『重写』")

    observeEvent(input$go, {
      req(nzchar(input$text))
      status("调用中……")
      msg <- prompt_rewrite(input$text, action = input$action)
      r <- tryCatch(
        llm_call(provider, messages = msg, model = model,
                 max_tokens = 2048L,
                 api_key = resolve_llm_key(provider, session)),
        error = function(e) {
          warning(sprintf("[降级] 重写: %s", safe_log_error(e)))
          list(text = sprintf("（失败: %s）", safe_log_error(e)),
               usage = list(prompt_tokens_hit=0L,
                            prompt_tokens_miss=0L,
                            completion_tokens=0L))
        })
      shiny::isolate(cost_add(shared, provider, model, r$usage))
      out_text(r$text %||% "")
      status(sprintf("完成 (本次 ≈ %d output tokens)", r$usage$completion_tokens))
    })

    output$status <- renderText(status())
    # I1: 原文与输出统一走 render_markdown_safe (内置 sanitizer).
    output$orig   <- renderUI(render_markdown_safe(
      input$text, fallback = "(尚未输入原文)"))
    output$out    <- renderUI(render_markdown_safe(
      out_text(),  fallback = "(尚未生成)"))
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
