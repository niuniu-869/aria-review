# R4-1: fct_screen_job.R 单测 (mock llm_call)
source(file.path("..", "..", "R", "fct_env.R"))
source(file.path("..", "..", "R", "fct_cost.R"))
source(file.path("..", "..", "R", "fct_llm_deepseek.R"))
source(file.path("..", "..", "R", "fct_llm.R"))
source(file.path("..", "..", "R", "fct_prompts.R"))
source(file.path("..", "..", "R", "fct_screen_job.R"))

test_that("screen_job_run 处理全部文档并写回 cost_log", {
  corpus_df <- data.frame(
    TI = c("A","B","C"), AB = c("a","b","c"), DE = c("x","y","z"),
    DI = c("10.1/a","10.1/b","10.1/c"), stringsAsFactors = FALSE)
  mockery::stub(screen_job_run, "llm_call", function(provider, messages, ...) {
    list(text = '{"relevance":7,"reason":"R"}',
         usage = list(prompt_tokens_hit = 0L,
                      prompt_tokens_miss = 10L,
                      completion_tokens = 5L))
  })
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  job <- screen_job_new(corpus_df, topic = "topic", shared = shared)
  out <- suppressWarnings(screen_job_run(job))
  expect_equal(nrow(out), 3L)
  expect_true(all(out$relevance == 7L))
  expect_true(all(out$status == "ok"))
  expect_equal(nrow(shiny::isolate(shared$cost_log)), 3L)
})

test_that("screen_job_run 在 LLM 错误时不丢条, 标 failed", {
  corpus_df <- data.frame(TI="A", AB="a", DE="x", DI="10.1/a",
                          stringsAsFactors=FALSE)
  mockery::stub(screen_job_run, "llm_call", function(...) stop("simulated"))
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  job <- screen_job_new(corpus_df, topic = "topic", shared = shared)
  suppressWarnings(out <- screen_job_run(job))
  expect_equal(nrow(out), 1L)
  expect_true(is.na(out$relevance))
  expect_equal(out$status, "failed")
})

test_that("screen_job_run 在 JSON 解析失败时降级", {
  corpus_df <- data.frame(TI="A", AB="a", DE="x", DI="10.1/a",
                          stringsAsFactors=FALSE)
  mockery::stub(screen_job_run, "llm_call", function(...) {
    list(text = "this is not JSON",
         usage = list(prompt_tokens_hit=0L, prompt_tokens_miss=1L,
                      completion_tokens=1L))
  })
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  job <- screen_job_new(corpus_df, topic = "topic", shared = shared)
  suppressWarnings(out <- screen_job_run(job))
  expect_equal(nrow(out), 1L)
  expect_true(is.na(out$relevance))
  expect_equal(out$status, "failed")
})

test_that("cancel_flag=TRUE 时提前停止, 已处理结果保留", {
  corpus_df <- data.frame(TI=letters[1:5], AB=letters[1:5], DE=letters[1:5],
                          DI=paste0("10.1/", letters[1:5]),
                          stringsAsFactors=FALSE)
  flag <- FALSE
  mockery::stub(screen_job_run, "llm_call", function(...) {
    flag <<- TRUE  # 第一篇后让 cancel 返回 TRUE
    list(text='{"relevance":5,"reason":"x"}',
         usage = list(prompt_tokens_hit=0L,prompt_tokens_miss=1L,completion_tokens=1L))
  })
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  job <- screen_job_new(corpus_df, topic = "topic", shared = shared,
                         cancel_flag = function() flag)
  out <- suppressWarnings(screen_job_run(job))
  # 第一篇做完, cancel_flag 翻 TRUE; 第二篇前检查就退出
  expect_equal(nrow(out), 1L)
})

test_that("空 corpus 直接返回空 data.frame, 不调 LLM", {
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  job <- screen_job_new(
    data.frame(TI=character(0), AB=character(0), DE=character(0), DI=character(0),
               stringsAsFactors=FALSE),
    topic = "x", shared = shared)
  out <- suppressWarnings(screen_job_run(job))
  expect_equal(nrow(out), 0L)
})
