# R/fct_cost.R — 三档计费 + 会话累计
#
# DeepSeek 价目 (2026-05-20 官网, CNY / 百万 token):
#   v4-flash:  cache_hit=0.02  cache_miss=1.00   output=2.00
#   v4-pro:    cache_hit=0.025 cache_miss=3.00   output=6.00 (优惠至 2026-05-31)
#              过期回退到 list_price (0.10 / 12 / 24)

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a

#' 空 cost_log 的 schema (在 server 内用于初始化 shared$cost_log)
cost_log_empty <- function() {
  data.frame(
    ts                  = as.POSIXct(character(0)),
    provider            = character(0),
    model               = character(0),
    prompt_tokens_hit   = integer(0),
    prompt_tokens_miss  = integer(0),
    completion_tokens   = integer(0),
    cost_cny            = numeric(0),
    stringsAsFactors    = FALSE
  )
}

#' 解析 effective_until ISO 时间; yaml 可能给 character 或 POSIXct
.parse_until <- function(s) {
  if (is.null(s) || (is.character(s) && !nzchar(s))) return(NA)
  if (inherits(s, "POSIXt")) return(as.POSIXct(s))
  tryCatch(as.POSIXct(sub("(\\+\\d{2})(\\d{2})$", "\\1:\\2", s),
                       format = "%Y-%m-%dT%H:%M:%S%z"),
           error = function(e) NA)
}

#' 单次调用成本估算 (CNY)
#' @param cfg get_llm_config() 的返回
cost_estimate <- function(provider, model,
                          prompt_tokens_hit, prompt_tokens_miss,
                          completion_tokens,
                          cfg = NULL) {
  if (is.null(cfg)) cfg <- get_llm_config()
  prov <- cfg$providers[[provider]]
  if (is.null(prov)) return(NA_real_)
  prices <- prov$pricing[[model]]
  if (is.null(prices)) return(NA_real_)
  # 优惠期判定
  until <- .parse_until(prices$effective_until)
  if (!is.na(until) && Sys.time() > until && !is.null(prices$list_price)) {
    prices <- prices$list_price
  }
  (prompt_tokens_hit  / 1e6) * (prices$input_cache_hit  %||% 0) +
  (prompt_tokens_miss / 1e6) * (prices$input_cache_miss %||% 0) +
  (completion_tokens  / 1e6) * (prices$output           %||% 0)
}

#' 把一次调用追加到 shared$cost_log (reactiveValues)
#' @param shared shiny::reactiveValues 含 cost_log
#' @param usage list(prompt_tokens_hit, prompt_tokens_miss, completion_tokens)
cost_add <- function(shared, provider, model, usage, cfg = NULL) {
  hit  <- as.integer(usage$prompt_tokens_hit  %||% 0L)
  miss <- as.integer(usage$prompt_tokens_miss %||% 0L)
  cmp  <- as.integer(usage$completion_tokens  %||% 0L)
  cost <- tryCatch(
    cost_estimate(provider, model, hit, miss, cmp, cfg = cfg),
    error = function(e) NA_real_  # config.yml 不可用时优雅降级, 仅记 tokens
  )
  if (is.na(cost)) cost <- 0
  row <- data.frame(
    ts                 = Sys.time(),
    provider           = provider,
    model              = model,
    prompt_tokens_hit  = hit,
    prompt_tokens_miss = miss,
    completion_tokens  = cmp,
    cost_cny           = cost,
    stringsAsFactors   = FALSE
  )
  shared$cost_log <- rbind(shared$cost_log, row)
  invisible(row)
}

#' 导出 CSV
cost_export_csv <- function(log_df, path) {
  utils::write.csv(log_df, path, row.names = FALSE, fileEncoding = "UTF-8")
  invisible(path)
}
