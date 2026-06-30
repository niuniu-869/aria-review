# R/fct_env.R — .env 与 config.yml 统一读取
#
# 安全约束:
#   · 永不缓存 key 值 (每次调用都从 Sys.getenv 即时取)
#   · 不打印 key, 不写入日志, 不返回到前端
#   · has_env() 仅回报存在性, 不返回值本身

#' 从 .env 文件加载键值对到 Sys 环境
#'
#' KISS 手解析, 不引入 dotenv 包. 支持 # 注释、空行、双/单引号包裹的值.
#' @param path .env 文件路径; 默认项目根
#' @return invisible TRUE = 加载成功, FALSE = 文件不存在
load_env <- function(path = ".env") {
  if (!file.exists(path)) return(invisible(FALSE))
  lines <- readLines(path, warn = FALSE, encoding = "UTF-8")
  for (ln in lines) {
    ln <- sub("^\\s+|\\s+$", "", ln)
    if (!nzchar(ln) || startsWith(ln, "#")) next
    m <- regmatches(ln, regexec("^([A-Za-z_][A-Za-z0-9_]*)\\s*=\\s*(.*)$", ln))[[1]]
    if (length(m) != 3) next
    k <- m[2]; v <- m[3]
    # 去引号
    if (nchar(v) >= 2 &&
        ((startsWith(v, "\"") && endsWith(v, "\"")) ||
         (startsWith(v, "'")  && endsWith(v, "'")))) {
      v <- substr(v, 2, nchar(v) - 1)
    }
    do.call(Sys.setenv, stats::setNames(list(v), k))
  }
  invisible(TRUE)
}

#' 仅返回环境变量是否存在 (UI 显示「已配置 / 未配置」用)
has_env <- function(name) nzchar(Sys.getenv(name, unset = ""))

#' 取环境变量值. 调用方应即用即弃, 不要存到 reactive / 全局变量
#' @return character(1); 未配置时 stop
get_env_value <- function(name) {
  v <- Sys.getenv(name, unset = "")
  if (!nzchar(v)) stop(sprintf("环境变量 %s 未配置", name))
  v
}

#' 向上查找 config.yml (从当前目录起, 最多走 5 层)
.find_config_yml <- function(start = ".", filename = "config.yml") {
  d <- normalizePath(start, mustWork = FALSE)
  for (i in 0:5) {
    cand <- file.path(d, filename)
    if (file.exists(cand)) return(cand)
    parent <- dirname(d)
    if (parent == d) break
    d <- parent
  }
  filename  # fallback (let read_yaml 报错)
}

#' 从 config.yml 读 llm 子段
#' @param path config.yml 路径 (默认在 cwd 或祖先目录寻找)
get_llm_config <- function(path = NULL) {
  if (!requireNamespace("yaml", quietly = TRUE))
    stop("缺少 yaml 包: install.packages('yaml')")
  if (is.null(path)) path <- .find_config_yml()
  cfg <- yaml::read_yaml(path)
  cfg$default$llm
}

#' 从 config.yml 读 pdf 子段
get_pdf_config <- function(path = NULL) {
  if (!requireNamespace("yaml", quietly = TRUE))
    stop("缺少 yaml 包: install.packages('yaml')")
  if (is.null(path)) path <- .find_config_yml()
  cfg <- yaml::read_yaml(path)
  cfg$default$pdf
}
