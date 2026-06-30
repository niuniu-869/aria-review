# mod_upload.R — 数据导入模块 (v0.6: + PubMed 入口 + 去重/元数据修复)

uploadUI <- function(id) {
  ns <- NS(id)
  tagList(
    page_header(LBL$menu_upload,
                "上传从 WoS / Scopus / PubMed 导出的文献数据 - 这页能回答: 我手里的数据怎么变成可分析的语料."),
    analysis_card(
      "上传文献数据 (WoS / Scopus)",
      desc = "支持 Web of Science (纯文本/BibTeX) 与 Scopus (BibTeX/CSV) 导出文件.",
      fileInput(ns("file"), "选择文件", accept = c(".txt", ".bib", ".csv"),
                buttonLabel = "浏览", placeholder = "未选择文件"),
      selectInput(ns("dbsource"), "数据库来源",
                  choices = c("Web of Science" = "wos", "Scopus" = "scopus")),
      selectInput(ns("format"), "文件格式",
                  choices = c("纯文本 plaintext" = "plaintext",
                              "BibTeX" = "bibtex", "CSV" = "csv")),
      actionButton(ns("parse"), "解析数据", status = "primary"),
      br(), br(),
      verbatimTextOutput(ns("status"))
    ),
    # v0.6 (F4): PubMed 三种入口
    analysis_card(
      "从 PubMed 导入 (F4)",
      desc = "PMID 列表 / .nbib 文件 / PubMed 检索式三选一; 自动转为可分析语料, 可与 WoS/Scopus 合并.",
      radioButtons(ns("pm_mode"), "导入方式",
                   choices = c("粘贴 PMID 列表" = "pmid",
                               "上传 .nbib 文件" = "nbib",
                               "PubMed 检索式"   = "query"),
                   selected = "pmid", inline = TRUE),
      conditionalPanel(
        sprintf("input['%s'] == 'pmid'", ns("pm_mode")),
        textAreaInput(ns("pm_pmids"), "PMID (逗号或换行分隔)", rows = 3,
                      placeholder = "例: 31452104, 33301246, 34567890")
      ),
      conditionalPanel(
        sprintf("input['%s'] == 'nbib'", ns("pm_mode")),
        fileInput(ns("pm_file"), "选择 .nbib 文件", accept = c(".nbib", ".txt"))
      ),
      conditionalPanel(
        sprintf("input['%s'] == 'query'", ns("pm_mode")),
        textInput(ns("pm_query"), "PubMed 检索式",
                  placeholder = '例: "machine learning"[Title] AND 2024[PDAT]'),
        numericInput(ns("pm_max"), "最多拉取条数", value = 100, min = 1, max = 500)
      ),
      actionButton(ns("pm_go"), "从 PubMed 导入", status = "primary",
                   icon = icon("download")),
      br(), br(),
      verbatimTextOutput(ns("pm_status"))
    ),
    # v0.6 (N1): 去重 + 元数据修复
    analysis_card(
      "去重与元数据修复 (N1)",
      desc = "导入后自动按 DOI + 标题去重; 可选用 Crossref 回填缺失的 DOI/摘要.",
      uiOutput(ns("dedup_box")),
      actionButton(ns("enrich"), "用 Crossref 补全缺失元数据 (前 50 条)",
                   status = "secondary", icon = icon("wand-magic-sparkles")),
      verbatimTextOutput(ns("enrich_status"))
    ),
    analysis_card("数据预览", DT::DTOutput(ns("preview")))
  )
}

#' @param shared 可选 reactiveValues; 提供时把 PRISMA 自动填充快照写入 shared$prisma_autofill
#' @return reactive：解析后的语料 data.frame，未解析时为 NULL
uploadServer <- function(id, shared = NULL) {
  moduleServer(id, function(input, output, session) {
    corpus     <- reactiveVal(NULL)
    raw_count  <- reactiveVal(0L)   # 去重前原始条数 (PRISMA 识别数)
    dedup_rep  <- reactiveVal(NULL) # 去重报告

    # 统一收口: 任一来源解析出 corpus 后, 跑去重 + 写 PRISMA 自动填充
    accept_corpus <- function(M, source_label) {
      if (is.null(M) || !valid_corpus(M)) {
        corpus(NULL)
        return(sprintf("%s: 解析结果无效 (空或字段不匹配).", source_label))
      }
      n_raw <- nrow(M)
      raw_count(n_raw)
      dd <- tryCatch(dedup_corpus(M), error = function(e) NULL)
      if (!is.null(dd)) {
        corpus(dd$corpus)
        dedup_rep(dd$report)
        n_dup <- n_raw - nrow(dd$corpus)
        # 写 PRISMA 自动填充快照
        if (!is.null(shared)) {
          shared$prisma_autofill <- list(
            identified = n_raw,
            duplicates = n_dup,
            screened   = nrow(dd$corpus),
            excluded   = 0L,
            included   = nrow(dd$corpus))
        }
        sprintf("%s: 解析成功 %d 条, 去重后 %d 条 (移除 %d 重复).",
                source_label, n_raw, nrow(dd$corpus), n_dup)
      } else {
        corpus(M); dedup_rep(NULL)
        if (!is.null(shared))
          shared$prisma_autofill <- list(identified = n_raw, duplicates = 0L,
                                         screened = n_raw, excluded = 0L,
                                         included = n_raw)
        sprintf("%s: 解析成功 %d 条 (去重步骤跳过).", source_label, n_raw)
      }
    }

    # ── WoS / Scopus 文件 ──
    observeEvent(input$parse, {
      req(input$file)
      withProgress(message = "正在解析文献数据……", value = 0.5, {
        result <- tryCatch(
          import_corpus(input$file$datapath, input$dbsource, input$format),
          error = function(e) e)
      })
      if (inherits(result, "error")) {
        corpus(NULL)
        output$status <- renderText(paste0("解析失败：", conditionMessage(result),
                                            "\n请检查数据库来源与文件格式是否匹配。"))
      } else {
        output$status <- renderText(accept_corpus(result, "WoS/Scopus"))
      }
    })

    # ── PubMed 三入口 ──
    observeEvent(input$pm_go, {
      msg <- withProgress(message = "正在从 PubMed 导入……", value = 0.5, {
        tryCatch({
          M <- switch(input$pm_mode,
            pmid = {
              ids <- unlist(strsplit(input$pm_pmids %||% "", "[,\\s]+"))
              ids <- ids[nzchar(ids)]
              if (!length(ids)) stop("未输入 PMID")
              pubmed_to_corpus(ids)
            },
            nbib = {
              req(input$pm_file)
              nbib_parse(input$pm_file$datapath)
            },
            query = {
              if (!nzchar(input$pm_query %||% "")) stop("未输入检索式")
              pubmed_to_corpus(input$pm_query,
                               max_records = as.integer(input$pm_max %||% 100L))
            })
          accept_corpus(M, "PubMed")
        }, error = function(e) sprintf("PubMed 导入失败: %s", conditionMessage(e)))
      })
      output$pm_status <- renderText(msg)
    })

    # ── 元数据修复 (按需, 走 Crossref 网络) ──
    observeEvent(input$enrich, {
      req(corpus())
      msg <- withProgress(message = "Crossref 补全中……", value = 0.5, {
        tryCatch({
          en <- enrich_metadata(corpus())
          corpus(en$corpus)
          n_fix <- tryCatch(nrow(en$report), error = function(e) 0L)
          sprintf("元数据修复完成: 补全 %d 处.", n_fix %||% 0L)
        }, error = function(e) sprintf("元数据修复失败: %s", conditionMessage(e)))
      })
      output$enrich_status <- renderText(msg)
    })

    # ── 去重报告 infoBox ──
    output$dedup_box <- renderUI({
      M <- corpus()
      if (is.null(M)) return(div(class = "text-muted", "尚未导入数据."))
      rep <- dedup_rep()
      n_dup <- if (!is.null(rep))
        sum(rep$decision != "kept", na.rm = TRUE) else 0L
      n_doi <- sum(nzchar(M$DI %||% ""), na.rm = TRUE)
      div(class = "row",
        div(class = "col-sm-4",
            tags$div(class = "small text-muted", "当前语料"),
            tags$h4(sprintf("%d 篇", nrow(M)))),
        div(class = "col-sm-4",
            tags$div(class = "small text-muted", "已去重"),
            tags$h4(sprintf("%d 重复", n_dup))),
        div(class = "col-sm-4",
            tags$div(class = "small text-muted", "含 DOI"),
            tags$h4(sprintf("%d 篇", n_doi))))
    })

    output$preview <- DT::renderDT({
      req(corpus())
      cols <- intersect(c("AU", "TI", "SO", "PY", "TC", "DI"), names(corpus()))
      DT::datatable(corpus()[, cols, drop = FALSE],
                    options = list(pageLength = 10, scrollX = TRUE))
    })

    corpus
  })
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
