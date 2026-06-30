# fct_api_probe.R — 启动时探测三个外部 API 的可用性
#
# 用途: 欢迎页卡片绑定状态灯, 让新人在点击入口前就知道这条路径能不能走.
# 比"点了报错"体验更透明.
#
# 探测策略:
#   · OpenAlex / Crossref: 公共 API, 不需要 key, 做一次轻量 HEAD/GET 验联通
#   · DeepSeek:           需要 key + 收费, 不真实调用, 只检查 has_env(key)
#                          (真实调用会消耗 token, 启动时不值得; 用户进
#                           AI 模块再让它真实失败给出错误信息)
#
# 设计原则:
#   · 探测全部并行 (用 future + promises 在 Shiny 端) — 这里只给同步实现,
#     调用方决定要不要 promise 化 (启动时 < 5s 总耗时是可接受的)
#   · 单个探测短超时 (3s), 网络抖动不致命
#   · 永远不抛错; 返回 list($ok, $msg) 即可

#' 探测单个 URL 是否可达
#'
#' @param url           目标 URL (尽量轻量, e.g. ?per-page=1)
#' @param timeout       秒
#' @return list(ok = TRUE/FALSE, msg = character)
.probe_url <- function(url, timeout = 3) {
  req <- httr2::request(url) |>
    httr2::req_timeout(timeout) |>
    httr2::req_user_agent("BiblioCN/0.1 (api-probe)") |>
    httr2::req_error(is_error = function(resp) FALSE)
  resp <- tryCatch(httr2::req_perform(req), error = function(e) NULL)
  if (is.null(resp)) return(list(ok = FALSE, msg = "网络不通"))
  status <- httr2::resp_status(resp)
  if (status < 400) list(ok = TRUE,  msg = "在线")
  else              list(ok = FALSE, msg = sprintf("HTTP %d", status))
}

#' 一次性探测 OpenAlex / Crossref / DeepSeek 三个端点
#'
#' 启动时调一次, 结果缓存到 reactiveVal 给欢迎页用. 用户点"重试"才再探一次.
#'
#' @param mailto    OpenAlex polite pool 邮箱; 默认从环境变量取
#' @return list of list(ok, msg), keys: openalex / crossref / deepseek
probe_apis <- function(mailto = NULL) {
  mailto <- mailto %||%
    (if (nzchar(Sys.getenv("OPENALEX_EMAIL")))
       Sys.getenv("OPENALEX_EMAIL")
     else "aria-review@users.noreply.github.com")

  # OpenAlex: 用 /works 轻量查询当探测端点
  openalex <- .probe_url(
    sprintf("https://api.openalex.org/works?per-page=1&mailto=%s",
            utils::URLencode(mailto, reserved = TRUE)))

  # Crossref: /works?rows=1 一篇即可
  crossref <- .probe_url("https://api.crossref.org/works?rows=1")

  # DeepSeek: 只看 key 是否配置 (避免消耗 token)
  has_key <- nzchar(Sys.getenv("DEEPSEEK_API_KEY", unset = ""))
  deepseek <- if (has_key) list(ok = TRUE,  msg = "已配置")
              else         list(ok = FALSE, msg = "未配置 key")

  list(openalex = openalex, crossref = crossref, deepseek = deepseek)
}

#' 把探测结果格式化为简短状态字符串 (用于卡片右上角徽章)
#'
#' @param probe   probe_apis() 返回值的单项, list(ok, msg)
#' @return character: "● 在线" / "● 未配置 key" / "● 网络不通"
api_status_badge <- function(probe) {
  dot <- if (isTRUE(probe$ok)) "●"  # ● 实心圆
         else                  "○"  # ○ 空心圆
  paste(dot, probe$msg)
}

#' 卡片是否可用 (UI 上灰显/正常的判断)
api_card_enabled <- function(probe) isTRUE(probe$ok)
