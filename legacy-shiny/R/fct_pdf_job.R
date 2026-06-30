# R/fct_pdf_job.R — PDF 获取 Job
#
# 关键设计:
#   1. session 隔离: 输出目录与 worklist 必须在 session 临时目录, 拒绝共享路径
#   2. DOI 白名单: ^10\.[0-9]{4,9}/.+, 拒绝命令注入
#   3. 真异步: processx::process$new() + later::later 周期 poll stdout
#   4. 取消: pdf_job_kill() 调用 proc$kill()

# DOI 标准格式正则: 前缀 10. + 4-9 位数字 + / + 后缀 (Crossref 推荐)
DOI_PATTERN <- "^10\\.[0-9]{4,9}/[-._;()/:A-Za-z0-9]+$"

#' 构造 Job (尚未启动). 输出目录创建在 session_dir 内.
#'
#' @param dois character 向量
#' @param session_dir Shiny session 临时根目录 (绝不能是 /srv/shared/pdfs)
#' @return list(dois, session_dir, out_dir, worklist, dois_file, status)
pdf_job_new <- function(dois, session_dir) {
  # 安全: 拒绝共享目录
  if (grepl("/srv/shared/(pdfs|worklist)", session_dir,
             ignore.case = TRUE)) {
    stop("session_dir 不允许位于共享路径 /srv/shared/pdfs; 必须 session 隔离")
  }
  # 安全: DOI 白名单
  bad <- dois[!grepl(DOI_PATTERN, dois)]
  if (length(bad)) {
    stop(sprintf("非法 DOI 输入: %s",
                 paste(utils::head(bad, 3), collapse = ", ")))
  }
  dir.create(session_dir, recursive = TRUE, showWarnings = FALSE)
  out_dir   <- file.path(session_dir, "pdfs")
  dir.create(out_dir, showWarnings = FALSE)
  worklist  <- file.path(session_dir, "worklist.csv")
  dois_file <- file.path(session_dir, "dois.txt")
  writeLines(dois, dois_file, useBytes = TRUE)
  list(
    dois        = dois,
    session_dir = session_dir,
    out_dir     = out_dir,
    worklist    = worklist,
    dois_file   = dois_file,
    proc        = NULL,
    status      = "pending"
  )
}

#' 启动 Job (异步, 立即返回; 通过 on_event 回调推进度)
#'
#' @param on_event function(ev) 收到 JSON line 事件时调用
#' @param cancel_flag function() 返回 TRUE 即取消 (用于 reactiveVal)
pdf_job_run <- function(job,
                        on_event = function(ev) NULL,
                        cancel_flag = function() FALSE,
                        cfg = NULL) {
  if (is.null(cfg)) cfg <- get_pdf_config()
  pipeline <- cfg$lit_pipeline_path
  python   <- cfg$python_bin %||% "python3"
  if (!file.exists(pipeline)) {
    warning(sprintf("[降级] lit_pipeline.py 不存在: %s", pipeline))
    job$status <- "skipped"
    return(job)
  }
  if (!nzchar(Sys.which(python))) {
    warning(sprintf("[降级] %s 不可用", python))
    job$status <- "skipped"
    return(job)
  }

  proc <- processx::process$new(
    command = python,
    args = c(pipeline, "dois", job$dois_file,
             "--out-dir",  job$out_dir,
             "--worklist", job$worklist,
             "--json-progress"),
    stdout = "|", stderr = "|", cleanup = TRUE
  )
  job$proc   <- proc
  job$status <- "running"

  # 周期 poll: 用 local 闭包持有 proc / on_event / cancel_flag
  poll <- function() {
    if (!proc$is_alive()) {
      # 排空剩余 stdout
      remaining <- tryCatch(proc$read_all_output_lines(), error = function(e) character(0))
      for (ln in remaining) {
        ev <- tryCatch(jsonlite::fromJSON(ln, simplifyVector = TRUE),
                       error = function(e) NULL)
        if (!is.null(ev)) on_event(ev)
      }
      job$status <<- if (cancel_flag()) "cancelled" else "done"
      return(invisible())
    }
    if (cancel_flag()) {
      tryCatch(proc$kill(), error = function(e) NULL)
      job$status <<- "cancelled"
      return(invisible())
    }
    lns <- tryCatch(proc$read_output_lines(n = 50), error = function(e) character(0))
    for (ln in lns) {
      ev <- tryCatch(jsonlite::fromJSON(ln, simplifyVector = TRUE),
                     error = function(e) NULL)
      if (!is.null(ev)) on_event(ev)
    }
    later::later(poll, delay = 0.5)
  }
  later::later(poll, delay = 0.1)
  job
}

#' 强杀 Job
pdf_job_kill <- function(job) {
  if (!is.null(job$proc) && job$proc$is_alive()) {
    tryCatch(job$proc$kill(), error = function(e) NULL)
  }
  job$status <- "cancelled"
  job
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
