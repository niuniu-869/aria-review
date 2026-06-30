# R/mod_ai_chat.R — 与语料对话 (多轮检索式问答)
#
# 每次提问都把当前 corpus 的 build_context 结果作为新 context 注入,
# 同时携带 history 维持多轮对话.

aiChatUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_ai_chat,
                "这页能做: 跟你的文献库聊天 - \"这堆论文里关于 X 的主要观点是什么?\" \"哪几篇用了 DID 方法?\""),
    analysis_card(
      "与语料对话",
      desc = "基于已上传语料的多轮中文问答; 每次提问都注入 build_context 上下文.",
      uiOutput(ns("history")),
      textAreaInput(ns("query"), "你的问题", rows = 3,
                    placeholder = "例: 这批文献的主要研究主题是什么?"),
      fluidRow(
        column(4, actionButton(ns("send"),  "发送", status = "primary",
                                icon = icon("paper-plane"))),
        # FINDING-004 修复: 清空对话是 destructive (会丢失整段对话历史),
        # 应当用 danger 而非 secondary, 与全站语义一致.
        column(4, actionButton(ns("clear"), "清空对话",
                                status = "danger", icon = icon("trash")))
      )
    )
  )
}

aiChatServer <- function(id, corpus, shared,
                         provider = "deepseek",
                         model = "deepseek-v4-flash") {
  moduleServer(id, function(input, output, session) {

    history <- reactiveVal(list())

    observeEvent(input$send, {
      req(corpus())
      req(nzchar(input$query))
      ctx <- tryCatch(build_context(corpus()),
                      error = function(e) list(corpus_summary = list(n_docs = nrow(corpus()))))
      msgs <- prompt_chat(history = history(), ctx = ctx, query = input$query)
      r <- tryCatch(
        llm_call(provider, messages = msgs, model = model,
                 max_tokens = 2048L,
                 api_key = resolve_llm_key(provider, session)),
        error = function(e) {
          warning(sprintf("[降级] 对话: %s", safe_log_error(e)))
          list(text = sprintf("（失败: %s）", safe_log_error(e)),
               usage = list(prompt_tokens_hit=0L,
                            prompt_tokens_miss=0L,
                            completion_tokens=0L))
        })
      shiny::isolate(cost_add(shared, provider, model, r$usage))
      h <- history()
      h[[length(h) + 1]] <- list(role = "user",      content = input$query)
      h[[length(h) + 1]] <- list(role = "assistant", content = r$text %||% "")
      history(h)
      updateTextAreaInput(session, "query", value = "")
    })

    observeEvent(input$clear, history(list()))

    # FINDING-010 修复: 空状态时不再只显示一行小灰字, 而是给出一个
    # 虚线边框 + icon + 文案的占位容器, 让用户知道对话会展开在哪里.
    output$history <- renderUI({
      h <- history()
      if (!length(h)) {
        return(div(class = "biblio-chat-empty",
                   icon("comments", class = "biblio-chat-empty-icon"),
                   p("对话会在这里展开"),
                   p(class = "biblio-chat-empty-hint",
                     "在下方输入框写下问题, 点击『发送』开始.")))
      }
      # I1 (spec §1.3): 统一 markdown 渲染入口, 不再用 htmlEscape + <br/>.
      # render_chat_bubble 内部走 render_markdown_safe -> sanitizer, XSS 安全.
      do.call(tagList, lapply(h, function(m) {
        render_chat_bubble(m$role, m$content)
      }))
    })
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
