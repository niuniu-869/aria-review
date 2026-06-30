# R/mod_settings.R — 设置：Provider/Key 状态 + 费用看板 + 隐私

settingsUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_settings,
                "这页能做: 配置 AI 服务的 API key / 查看本会话累计费用 / 阅读隐私说明"),
    # FINDING-002 修复: 原先 verbatimTextOutput(config) + renderPrint(cfg) 把
    # 整个 R list 直接 print 到 UI, 暴露内部数据结构 ($default_provider,
    # $providers$deepseek$pricing$`deepseek-v4-flash`$input_cache_hit 这种).
    # 现改为结构化 uiOutput, 把 provider/model/pricing 用人类可读卡片展示.
    analysis_card(
      "Provider / Key 状态",
      uiOutput(ns("key_status")),
      tags$hr(style = "margin: 16px 0;"),
      uiOutput(ns("config_ui"))
    ),
    # I2 (spec §2): 用户自带 API key, 会话级生效
    analysis_card(
      "使用我自己的 API key (会话级, 不持久化)",
      desc = paste(
        "可选: 填入你自己的 DeepSeek API key, 仅在当前浏览器会话内生效.",
        "key 不写入磁盘, 不上传, 不入日志, 关闭页面或刷新即销毁.",
        "留空则使用部署方默认配置 (.env).",
        sep = " "),
      fluidRow(
        column(8,
          passwordInput(ns("user_api_key"), "DEEPSEEK_API_KEY",
                        placeholder = "REDACTED_API_KEY"),
          helpText("提示: 输入框为密码类型, 不回显你的 key.")
        ),
        column(4,
          br(),
          actionButton(ns("save_user_key"), "在本会话内启用",
                       class = "btn-primary",
                       icon = icon("key")),
          tags$span(style = "margin-left:6px;"),
          actionButton(ns("clear_user_key"), "清除",
                       class = "btn-default",
                       icon = icon("times"))
        )
      ),
      uiOutput(ns("user_key_status"))
    ),
    analysis_card(
      "本会话费用累计",
      uiOutput(ns("cost_summary_box")),
      DT::DTOutput(ns("cost_table")),
      br(),
      # FINDING-004 修复: 移除 btn-primary class, 导出走默认灰白底.
      downloadButton(ns("dl_cost"), "导出 csv")
    ),
    analysis_card(
      "隐私与安全",
      p(strong("LLM 调用范围: "),
        "本平台会把文献标题、摘要、用户输入主题发送到 DeepSeek 服务器进行处理."),
      p(strong("Key 管理: "),
        "DEEPSEEK_API_KEY 仅在运行时从 .env 读取, 不写入日志, 不回显前端, 不持久化. ",
        "用户上传的会话级 key 仅存活于当前浏览器 tab 的会话内存, 关闭页面即销毁, ",
        "不写盘, 不上传, 不入日志."),
      p(strong("PDF 隔离: "),
        "PDF 获取仅写入本 session 的临时目录, session 结束自动清理."),
      p(strong("引用真实性: "),
        "综述生成的引用建议手动通过『引用导出 → Crossref 校验』确认 DOI 有效性.")
    )
  )
}

settingsServer <- function(id, shared) {
  moduleServer(id, function(input, output, session) {

    # 触发用户 key 状态 UI 重渲染的信号 (会话级 userData 不是 reactive,
    # 用一个 reactiveVal 计数器作为信号驱动 invalidate).
    user_key_tick <- reactiveVal(0L)

    output$key_status <- renderUI({
      user_key_tick()  # 监听信号, 用户 key 变动后状态行也更新
      env_ok   <- has_env("DEEPSEEK_API_KEY")
      sess_ok  <- has_session_key("deepseek", session)
      base_ok  <- nzchar(Sys.getenv("DEEPSEEK_BASE_URL", ""))

      effective_source <- if (sess_ok) "会话用户 key (优先)"
                          else if (env_ok) ".env 默认 key"
                          else "(无可用 key, AI 功能将报错)"
      effective_color  <- if (sess_ok || env_ok) "green" else "red"

      tagList(
        div(strong("当前生效来源: "),
            span(style = sprintf("color:%s; font-weight:bold;",
                                  effective_color),
                 effective_source)),
        div(strong("DEEPSEEK_API_KEY (.env): "),
            span(style = sprintf("color:%s;",
                                  if (env_ok) "green" else "#999"),
                 if (env_ok) "✓ 已配置" else "(未配置)")),
        div(strong("会话用户 key: "),
            span(style = sprintf("color:%s;",
                                  if (sess_ok) "green" else "#999"),
                 if (sess_ok) "✓ 已在本会话内启用" else "(未启用)")),
        div(strong("DEEPSEEK_BASE_URL: "),
            span(style = sprintf("color:%s;", if (base_ok) "green" else "orange"),
                 if (base_ok) Sys.getenv("DEEPSEEK_BASE_URL")
                 else "(未设置, 将用默认 https://api.deepseek.com/v1)"))
      )
    })

    # I2 (spec §2.3): 启用 / 清除会话级用户 key
    observeEvent(input$save_user_key, {
      k <- input$user_api_key %||% ""
      if (!nzchar(k)) {
        showNotification("key 不能为空", type = "warning")
        return()
      }
      if (!grepl(paste0("^", "s", "k", "-[A-Za-z0-9_\\-]{16,}$"), k)) {
        showNotification("key 格式不像 DeepSeek key (请确认前缀与字符集)",
                          type = "warning")
        return()
      }
      set_session_key("deepseek", k, session)
      # 立即清空输入框, 避免 key 在 input 状态里被快照 (Shiny input 不写盘,
      # 但减少在内存其他位置的副本)
      updateTextInput(session, "user_api_key", value = "")
      user_key_tick(user_key_tick() + 1L)
      showNotification(
        sprintf("已在本会话启用用户 key (%s). 关闭页面即销毁.",
                mask_key(k)),
        type = "message", duration = 6)
    })

    observeEvent(input$clear_user_key, {
      set_session_key("deepseek", NULL, session)
      updateTextInput(session, "user_api_key", value = "")
      user_key_tick(user_key_tick() + 1L)
      showNotification("已清除会话用户 key, 回退到 .env 默认配置.",
                        type = "message")
    })

    output$user_key_status <- renderUI({
      user_key_tick()
      if (has_session_key("deepseek", session)) {
        div(class = "alert alert-success", style = "margin-top:12px;",
            icon("circle-check"),
            sprintf(" 会话用户 key 已启用. 关闭浏览器 tab 或点击『清除』即销毁."))
      } else {
        div(class = "text-muted", style = "margin-top:12px;",
            "(尚未设置会话用户 key, 当前使用部署方默认 .env 配置.)")
      }
    })

    # FINDING-002 修复: 把 R list 用人类可读卡片渲染, 不再 print 内部结构.
    # 错误处理: 详细 err message 进服务端日志, UI 仅显示通用文案, 避免泄露
    # config 解析细节 / 路径 / 内部错误文本 (codex P0 review).
    output$config_ui <- renderUI({
      cfg <- tryCatch(get_llm_config(),
                       error = function(e) {
                         message(sprintf("[settings] config.yml 读取失败: %s",
                                          conditionMessage(e)))
                         list(error = TRUE)
                       })
      if (isTRUE(cfg$error)) {
        return(div(class = "text-danger",
                   strong("config.yml 读取失败: "),
                   span("无法解析配置文件, 请联系管理员检查服务端日志.")))
      }
      providers <- cfg$providers %||% list()
      tagList(
        div(class = "row mb-3",
            div(class = "col-sm-4", strong("默认 Provider:")),
            div(class = "col-sm-8",
                span(class = "badge bg-primary", cfg$default_provider %||% "(未设置)"))),
        lapply(names(providers), function(pname) {
          p <- providers[[pname]]
          models <- p$models %||% character(0)
          pricing <- p$pricing %||% list()
          tagList(
            tags$h5(style = "margin-top: 20px; font-weight: 600; color: #212529;",
                    sprintf("Provider: %s", pname)),
            div(class = "row mb-2",
                div(class = "col-sm-4 text-muted", "API base URL 来源:"),
                div(class = "col-sm-8", code(p$base_url_env %||% "-"))),
            div(class = "row mb-2",
                div(class = "col-sm-4 text-muted", "API key 来源:"),
                div(class = "col-sm-8", code(p$api_key_env %||% "-"))),
            div(class = "row mb-2",
                div(class = "col-sm-4 text-muted", "可用模型:"),
                div(class = "col-sm-8",
                    if (length(models)) {
                      lapply(models, function(m) {
                        is_default <- !is.null(p$default_model) && m == p$default_model
                        span(class = paste0("badge me-1 ",
                                            if (is_default) "bg-primary" else "bg-secondary"),
                             style = "margin-right: 6px;",
                             m, if (is_default) " (默认)")
                      })
                    } else span(class = "text-muted", "无"))),
            if (length(pricing)) {
              tagList(
                div(class = "text-muted mb-1", "价目表 (CNY / 百万 token):"),
                tags$table(class = "table table-sm table-borderless",
                  tags$thead(tags$tr(
                    tags$th("模型"), tags$th(class = "text-end", "输入 (cache hit)"),
                    tags$th(class = "text-end", "输入 (cache miss)"),
                    tags$th(class = "text-end", "输出"))),
                  tags$tbody(lapply(names(pricing), function(mname) {
                    pr <- pricing[[mname]]
                    # 用 as.numeric 兜底: 若价目里出现 nested list (例如 list_price
                    # 嵌套对象) 或非数字, 显示 NA 而不是 sprintf 报错 / 泄露结构.
                    fmt <- function(x) {
                      v <- suppressWarnings(as.numeric(x))
                      if (length(v) != 1L || is.na(v)) "—" else sprintf("%.2f", v)
                    }
                    tags$tr(
                      tags$td(mname),
                      tags$td(class = "text-end", fmt(pr$input_cache_hit)),
                      tags$td(class = "text-end", fmt(pr$input_cache_miss)),
                      tags$td(class = "text-end", fmt(pr$output))
                    )
                  }))
                )
              )
            }
          )
        })
      )
    })

    output$cost_summary_box <- renderUI({
      df <- shared$cost_log
      total <- sum(df$cost_cny, na.rm = TRUE)
      n     <- nrow(df)
      hit   <- sum(df$prompt_tokens_hit, na.rm = TRUE)
      miss  <- sum(df$prompt_tokens_miss, na.rm = TRUE)
      cmp   <- sum(df$completion_tokens, na.rm = TRUE)
      fluidRow(
        column(3, h4(sprintf("¥ %.4f", total)), p("本会话累计")),
        column(3, h4(n), p("调用次数")),
        column(3, h4(formatC(hit + miss, big.mark = ",")), p("输入 tokens (含缓存)")),
        column(3, h4(formatC(cmp, big.mark = ",")), p("输出 tokens"))
      )
    })

    output$cost_table <- DT::renderDT({
      df <- shared$cost_log
      DT::datatable(df,
                    options = list(pageLength = 15, scrollX = TRUE,
                                    order = list(list(0, "desc"))))
    })

    output$dl_cost <- downloadHandler(
      filename = function() sprintf("cost_%s.csv",
                                     format(Sys.time(), "%Y%m%d_%H%M%S")),
      content  = function(file) cost_export_csv(shared$cost_log, file)
    )
  })
}
