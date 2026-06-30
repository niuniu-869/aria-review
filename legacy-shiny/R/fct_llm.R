# R/fct_llm.R — provider 分发入口 + 错误脱敏
#
# 本期仅实现 deepseek; 未来加 zhipu / qwen / anthropic 时只需在 switch 中追加.

#' 统一 LLM 调用入口
#'
#' @param provider character; 当前仅 "deepseek"
#' @param messages list of list(role=..., content=...)
#' @param ... 透传给底层 provider 实现 (model, max_tokens, json_mode, temperature 等)
llm_call <- function(provider, messages, ...) {
  switch(provider,
    deepseek = deepseek_chat(messages = messages, ...),
    stop(sprintf("未知 provider: %s; 当前仅支持 'deepseek'", provider))
  )
}

#' 脱敏的错误日志格式化 (warning / 服务器日志前必经)
#'
#' 通配剥离: API key / Bearer * / Authorization 标头
safe_log_error <- function(e) {
  msg <- if (inherits(e, "condition")) conditionMessage(e) else as.character(e)
  msg <- gsub("Bearer\\s+\\S+", "Bearer ***REDACTED***", msg)
  msg <- gsub(paste0("s", "k", "-[A-Za-z0-9_\\-]+"), "***REDACTED_API_KEY***", msg)
  msg <- gsub("(?i)authorization[^\\n]*",
              "Authorization: ***REDACTED***", msg, perl = TRUE)
  msg
}
