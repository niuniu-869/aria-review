# R6-3: L3 端到端集成测试 (真实 LLM/PDF API)
# 默认 skip; 设置 RUN_LIVE_LLM=true / RUN_LIVE_PDF=true 才跑.
# 成本控制: 单测全部小批量 + max_tokens 偏小, 估算总开销 < ¥0.5

for (f in c("fct_env","fct_cost","fct_llm","fct_llm_deepseek",
            "fct_prompts","fct_review_templates","fct_cite","fct_crossref",
            "fct_pdf","fct_pdf_job","fct_screen_job","fct_context","fct_analysis"))
  source(file.path("..","..","R", paste0(f, ".R")))
load_env("../../.env")

# 真实语料: 200 篇 WoS MD&A 文献综述方向 (用户提供)
.corpus_path <- "../../data/wos_mda_200.txt"
.fixture_corpus <- function(n = NA_integer_) {
  M <- bibliometrix::convert2df(file = .corpus_path,
                                 dbsource = "wos", format = "plaintext")
  if (!is.na(n)) M <- utils::head(M, as.integer(n))
  M
}

# ───────────────────────────────────────────────────────────────────────────

test_that("E2E-0 真实语料解析成功 (200 篇, 含 DI)", {
  M <- .fixture_corpus()
  expect_equal(nrow(M), 200L)
  expect_true("DI" %in% names(M))
  expect_gt(sum(nzchar(ifelse(is.na(M$DI), "", M$DI))), 150L)
})

test_that("E2E-1 相关性筛选 5 篇真实文献", {
  testthat::skip_if_not(Sys.getenv("RUN_LIVE_LLM") == "true",
                        "需 RUN_LIVE_LLM=true")
  M <- .fixture_corpus(5L)
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  job <- screen_job_new(M,
                         topic = "Text analysis of MD&A in annual reports",
                         shared = shared,
                         model = "deepseek-chat")
  out <- screen_job_run(job)
  expect_equal(nrow(out), 5L)
  expect_true(sum(!is.na(out$relevance)) >= 3L)
  cat("\n[E2E-1] 费用: ¥",
      sprintf("%.4f", sum(shiny::isolate(shared$cost_log$cost_cny))), "\n")
})

test_that("E2E-2 综述写作 SCI Intro (3 章)", {
  testthat::skip_if_not(Sys.getenv("RUN_LIVE_LLM") == "true",
                        "需 RUN_LIVE_LLM=true")
  M <- .fixture_corpus(20L)
  ctx <- build_context(M, top_n = 5L)
  tpl <- template_for("sci_intro")
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  outs <- vapply(tpl$chapters, function(ch) {
    msg <- prompt_review(ctx, tpl, ch)
    r <- llm_call("deepseek", messages = msg, model = "deepseek-chat",
                  max_tokens = 500L)
    shiny::isolate(cost_add(shared, "deepseek", "deepseek-chat", r$usage))
    r$text %||% ""
  }, character(1))
  expect_true(all(nchar(outs) > 50L))
  cat("\n[E2E-2] 综述总字符:", sum(nchar(outs)),
      " 费用: ¥", sprintf("%.4f",
                             sum(shiny::isolate(shared$cost_log$cost_cny))), "\n")
})

test_that("E2E-3 Crossref 3 条已知 DOI 校验", {
  testthat::skip_if_not(Sys.getenv("RUN_LIVE_LLM") == "true",
                        "需 RUN_LIVE_LLM=true")
  # 从真实语料里取 3 个 DOI
  M <- .fixture_corpus()
  dois <- utils::head(M$DI[!is.na(M$DI) & nzchar(M$DI)], 3L)
  out <- verify_citations(dois)
  expect_equal(nrow(out), 3L)
  expect_true(sum(out$valid) >= 2L)
  cat("\n[E2E-3] DOI 校验: ", sum(out$valid), "/", length(dois), "通过\n")
})

test_that("E2E-4 PDF 获取 (session 隔离, RUN_LIVE_PDF=true 才跑)", {
  testthat::skip_if_not(Sys.getenv("RUN_LIVE_PDF") == "true",
                        "需 RUN_LIVE_PDF=true")
  library(later)
  td <- file.path(tempdir(), "e2e_pdf"); unlink(td, recursive = TRUE)
  # 用一个 OA 友好的 DOI (arXiv 上有, HTTP 直拿命中, 不走浏览器阶段)
  events <- list()
  job <- pdf_job_new("10.1109/cvpr.2016.90", session_dir = td)
  cfg <- list(lit_pipeline_path = "/srv/shared/lit_pipeline.py",
              python_bin = "python3")
  job <- pdf_job_run(job, cfg = cfg,
                     on_event = function(ev) events[[length(events)+1]] <<- ev)
  deadline <- Sys.time() + 120
  while (Sys.time() < deadline && !is.null(job$proc) && job$proc$is_alive()) {
    later::run_now(timeout = 1)
  }
  later::run_now(timeout = 3)
  expect_true(file.exists(job$worklist) ||
              length(list.files(job$out_dir, "\\.pdf$")) > 0L)
  expect_true(length(events) > 0L)
  # 验 session 隔离: 没有写到共享路径
  expect_false(file.exists("/srv/shared/pdfs/e2e_marker.pdf"))
})

test_that("E2E-5 翻译 + 总结 + 重写 各跑一次最小样本", {
  testthat::skip_if_not(Sys.getenv("RUN_LIVE_LLM") == "true",
                        "需 RUN_LIVE_LLM=true")
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  M <- .fixture_corpus(1L)

  # 翻译: 用真实摘要片段
  txt_en <- substr(M$AB[1] %||% "Sample abstract.", 1, 300)
  r1 <- llm_call("deepseek",
                 messages = prompt_translate(txt_en, "en2zh"),
                 model = "deepseek-chat", max_tokens = 500L)
  shiny::isolate(cost_add(shared, "deepseek", "deepseek-chat", r1$usage))
  expect_true(nzchar(r1$text))
  expect_true(grepl("[一-鿿]", r1$text))  # 含中文字符

  # 总结
  r2 <- llm_call("deepseek",
                 messages = prompt_summary(list(ti = M$TI[1] %||% "Test",
                                                 ab = M$AB[1] %||% "Test ab")),
                 model = "deepseek-chat", max_tokens = 400L)
  shiny::isolate(cost_add(shared, "deepseek", "deepseek-chat", r2$usage))
  expect_true(nzchar(r2$text))

  # 重写
  r3 <- llm_call("deepseek",
                 messages = prompt_rewrite("年报 MD&A 文本分析是会计研究的重要方向。", "compress"),
                 model = "deepseek-chat", max_tokens = 200L)
  shiny::isolate(cost_add(shared, "deepseek", "deepseek-chat", r3$usage))
  expect_true(nzchar(r3$text))

  cat("\n[E2E-5] 翻译/总结/重写 费用合计: ¥",
      sprintf("%.4f", sum(shiny::isolate(shared$cost_log$cost_cny))), "\n")
})

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
