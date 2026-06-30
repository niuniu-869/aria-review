# R/fct_llm_deepseek.R — DeepSeek HTTP 客户端 (OpenAI 兼容, 非流式)
#
# 安全约束:
#   · 永不打印 / 记录 Authorization header
#   · 禁用 req_verbose() (会把 headers 打到控制台)
#   · 错误对象在日志前剥离 request$headers

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a

#' 同步调用 DeepSeek chat completions (非流式)
#'
#' @param messages list of list(role=..., content=...)
#' @param model 默认 deepseek-v4-flash
#' @param max_tokens 默认 2048
#' @param json_mode TRUE 时强制 response_format = json_object
#' @param temperature 默认 0.3 (筛选/综述偏稳定)
#' @param api_key 显式注入 (优先级最高); NULL 则回退到 .env DEEPSEEK_API_KEY.
#'                会话级用户 key 由调用方 (mod_ai_*) 用 resolve_llm_key() 取得后传入.
#' @param base_url 默认从 .env 的 DEEPSEEK_BASE_URL 取
#' @param timeout_s 默认 60
#' @return list(text, usage = list(prompt_tokens_hit, prompt_tokens_miss, completion_tokens),
#'              model, finish_reason)
deepseek_chat <- function(messages,
                          model = "deepseek-v4-flash",
                          max_tokens = 2048L,
                          json_mode = FALSE,
                          temperature = 0.3,
                          api_key = NULL,
                          base_url = NULL,
                          timeout_s = 60L) {
  # 优先用显式传入的 api_key (来自 resolve_llm_key 的会话级 key),
  # 否则回退到 .env. 两者都缺则报错 (不静默失败).
  key <- NULL
  if (!is.null(api_key) && is.character(api_key) && nzchar(api_key)) {
    key <- api_key
  } else {
    key <- tryCatch(get_env_value("DEEPSEEK_API_KEY"),
                    error = function(e) NULL)
  }
  if (is.null(key) || !nzchar(key))
    stop("DEEPSEEK_API_KEY 未配置: 请在设置页填入你的 key, 或在 .env 中设置后重启应用")
  base <- base_url %||% Sys.getenv("DEEPSEEK_BASE_URL",
                                    "https://api.deepseek.com/v1")
  body <- list(
    model       = model,
    messages    = messages,
    max_tokens  = as.integer(max_tokens),
    temperature = temperature,
    stream      = FALSE
  )
  if (isTRUE(json_mode)) body$response_format <- list(type = "json_object")

  req <- httr2::request(paste0(sub("/$", "", base), "/chat/completions"))
  req <- httr2::req_method(req, "POST")
  req <- httr2::req_headers(req,
                            `Authorization` = paste("Bearer", key),
                            `Content-Type`  = "application/json")
  req <- httr2::req_body_json(req, body)
  req <- httr2::req_timeout(req, timeout_s)
  req <- httr2::req_throttle(req, rate = 30 / 60)
  req <- httr2::req_retry(req,
                          max_tries = 3,
                          backoff = function(i) 2 ^ i,
                          is_transient = function(resp) {
                            httr2::resp_status(resp) %in% c(429, 500, 502, 503, 504)
                          })
  req <- httr2::req_error(req, body = function(resp) {
    # 脱敏: 不把请求 body / headers 包进错误信息
    sprintf("DeepSeek API 错误 %d", httr2::resp_status(resp))
  })

  resp <- httr2::req_perform(req)
  body_raw <- if (is.raw(resp$body)) resp$body else charToRaw(as.character(resp$body))
  raw <- jsonlite::fromJSON(rawToChar(body_raw), simplifyVector = FALSE)
  ch  <- raw$choices[[1]]

  usage_raw <- raw$usage %||% list()
  cached <- (usage_raw$prompt_tokens_details %||% list())$cached_tokens %||% 0L
  prompt_total <- usage_raw$prompt_tokens %||% 0L

  list(
    text  = ch$message$content,
    usage = list(
      prompt_tokens_hit  = as.integer(cached),
      prompt_tokens_miss = as.integer(prompt_total - cached),
      completion_tokens  = as.integer(usage_raw$completion_tokens %||% 0L)
    ),
    model         = raw$model %||% model,
    finish_reason = ch$finish_reason %||% NA_character_
  )
}
