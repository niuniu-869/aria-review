# R/fct_session_key.R — 会话级用户 API key 解析
#
# 设计原则 (spec §2):
#   · 优先级: session$userData$user_api_key > .env > NA
#   · key 仅存活于当前会话 (浏览器 tab), 关闭即销毁
#   · 不写入 Sys.setenv (避免污染 R 进程全局)
#   · 不写入日志, 不持久化
#
# 用法:
#   server 里: api_key <- resolve_llm_key(session)
#   传给 llm_call(..., api_key = api_key)

#' 解析当前会话该用的 LLM key
#'
#' 优先级:
#'   1. session$userData$user_api_key (用户在设置页输入)
#'   2. Sys.getenv("DEEPSEEK_API_KEY") (部署方 .env 默认)
#'   3. NA_character_ (调用方需判空, 给出友好错误)
#'
#' @param provider character; 当前仅 "deepseek"
#' @param session  shiny session; 默认取当前 reactive domain
#' @return character 或 NA_character_
resolve_llm_key <- function(provider = "deepseek",
                            session  = shiny::getDefaultReactiveDomain()) {
  env_name <- switch(provider,
    deepseek = "DEEPSEEK_API_KEY",
    stop(sprintf("resolve_llm_key: 未知 provider %s", provider)))

  # 1. 会话用户 key (用 userData 子键, 支持多 provider 并存)
  k <- NULL
  if (!is.null(session) && !is.null(session$userData)) {
    bag <- session$userData$user_api_keys
    if (!is.null(bag)) k <- bag[[provider]]
  }
  if (!is.null(k) && is.character(k) && nzchar(k)) return(k)

  # 2. .env 回退
  k <- Sys.getenv(env_name, unset = NA_character_)
  if (!is.na(k) && nzchar(k)) return(k)

  NA_character_
}

#' 设置当前会话的用户 key (供 mod_settings 调用)
#'
#' @param provider character
#' @param key      character; 留空 / NULL 表示清除
#' @param session  shiny session
#' @return invisible(TRUE)
set_session_key <- function(provider = "deepseek", key,
                            session = shiny::getDefaultReactiveDomain()) {
  if (is.null(session)) return(invisible(FALSE))
  if (is.null(session$userData$user_api_keys)) {
    session$userData$user_api_keys <- list()
  }
  if (is.null(key) || !nzchar(key)) {
    session$userData$user_api_keys[[provider]] <- NULL
  } else {
    session$userData$user_api_keys[[provider]] <- key
  }
  invisible(TRUE)
}

#' 判断会话是否设置了用户 key (不返回 key 本身)
has_session_key <- function(provider = "deepseek",
                            session = shiny::getDefaultReactiveDomain()) {
  if (is.null(session) || is.null(session$userData)) return(FALSE)
  bag <- session$userData$user_api_keys
  if (is.null(bag)) return(FALSE)
  k <- bag[[provider]]
  !is.null(k) && is.character(k) && nzchar(k)
}

#' 脱敏显示 key (前 4 + 后 4 字符, 中间 ***)
#' 仅用于 UI 状态回显, 永不返回完整 key
mask_key <- function(key) {
  if (is.null(key) || is.na(key) || !nzchar(key)) return("(未设置)")
  n <- nchar(key)
  if (n <= 8L) return(strrep("*", n))
  paste0(substr(key, 1, 4), strrep("*", max(4, n - 8)), substr(key, n - 3, n))
}
