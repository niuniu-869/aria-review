# R/fct_screen_job.R — 批量相关性筛选 Job
#
# 设计:
#   · MVP 同步串行 (一次一篇), 复杂度低、易测; 并发优化推到 Phase 2.1 spike
#   · 每篇调 llm_call(json_mode=TRUE), 期望 {"relevance":0-10, "reason":"..."}
#   · 每篇结果即 cost_add 到 shared, UI 实时看到费用累计
#   · cancel_flag() 每篇前检查, TRUE 即提前 return

#' 构造 Job
#' @param corpus_df bibliometrix 语料 data.frame (含 TI/AB/DE/DI)
#' @param topic 研究主题 (≤ 200 字)
#' @param shared shiny::reactiveValues (含 cost_log)
#' @param cancel_flag function() → logical(1)
screen_job_new <- function(corpus_df, topic, shared,
                            provider = "deepseek",
                            model = "deepseek-v4-flash",
                            cancel_flag = function() FALSE,
                            api_key = NULL) {
  list(
    corpus      = corpus_df,
    topic       = topic,
    shared      = shared,
    provider    = provider,
    model       = model,
    cancel_flag = cancel_flag,
    api_key     = api_key,   # 会话级用户 key (mod_ai_screen 在 server 端解析后传入)
    results     = NULL,
    status      = "pending"
  )
}

#' 同步执行 Job, 每篇调 on_progress(i, n) 推进度
#' @return data.frame(doi, title, relevance, reason, status)
screen_job_run <- function(job, on_progress = function(i, n) NULL,
                            max_active = 5L) {
  n <- nrow(job$corpus)
  if (!n) {
    job$status <- "done"
    return(data.frame(doi=character(0), title=character(0),
                      relevance=integer(0), reason=character(0),
                      status=character(0), stringsAsFactors=FALSE))
  }
  res <- vector("list", n)
  for (i in seq_len(n)) {
    if (job$cancel_flag()) break
    row <- job$corpus[i, , drop = FALSE]
    doc <- list(ti = as.character(row$TI %||% ""),
                ab = as.character(row$AB %||% ""),
                de = as.character(row$DE %||% ""))
    msg <- prompt_screen(job$topic, doc)
    r <- tryCatch(
      llm_call(job$provider, messages = msg, model = job$model,
               max_tokens = 200L, json_mode = TRUE,
               api_key = job$api_key),
      error = function(e) {
        warning(sprintf("[降级] 筛选第 %d 条: %s", i, safe_log_error(e)))
        list(text = NA_character_,
             usage = list(prompt_tokens_hit = 0L,
                          prompt_tokens_miss = 0L,
                          completion_tokens = 0L))
      })
    if (!is.null(job$shared)) {
      shiny::isolate(cost_add(job$shared, job$provider, job$model, r$usage))
    }
    parsed <- tryCatch(jsonlite::fromJSON(r$text, simplifyVector = TRUE),
                       error = function(e) list(relevance = NA_integer_,
                                                reason = ""))
    res[[i]] <- data.frame(
      doi       = as.character(row$DI %||% ""),
      title     = as.character(row$TI %||% ""),
      relevance = suppressWarnings(as.integer(parsed$relevance %||% NA_integer_)),
      reason    = as.character(parsed$reason %||% ""),
      status    = if (is.na(parsed$relevance %||% NA)) "failed" else "ok",
      stringsAsFactors = FALSE
    )
    on_progress(i, n)
  }
  # 取消时 res 含 NULL 元素, 过滤掉
  res <- res[!vapply(res, is.null, logical(1))]
  out <- if (length(res)) do.call(rbind, res)
         else data.frame(doi=character(0), title=character(0),
                         relevance=integer(0), reason=character(0),
                         status=character(0), stringsAsFactors=FALSE)
  job$results <- out
  job$status  <- if (job$cancel_flag()) "cancelled" else "done"
  out
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
