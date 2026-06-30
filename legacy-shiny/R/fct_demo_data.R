# fct_demo_data.R — 演示数据加载器
#
# 设计目的: 给"完全没用过 WoS/Scopus 的新人"一个零门槛入口.
# 把本地 demo .txt 一键解析成 corpus, 让用户在没数据时也能完整体验产品.
#
# 公开仓库不提交 demo 语料文件；如需启用本入口，请在本地放置 data/demo/*.txt。
#
# 调用方:
#   · mod_welcome.R — "先用演示数据看看效果"卡片
#   · mod_upload.R  — 上传页底部"或者直接加载演示数据"按钮

#' 返回演示数据文件路径
#'
#' 优先返回 data/demo/ 下的本地演示文件; 缺失时返回 NULL, 调用方负责降级.
#' 用相对路径以适配 Shiny Server / shinyapps.io 各种部署.
#'
#' @return character(1) 路径, 或 NULL (文件不存在)
demo_data_path <- function() {
  candidates <- unique(c(
    Sys.glob("data/demo/*.txt"),
    Sys.glob(file.path(getwd(), "data/demo/*.txt"))
  ))
  hit <- candidates[file.exists(candidates)]
  if (length(hit)) hit[1] else NULL
}

#' 演示数据元信息 (给欢迎页卡片展示)
#'
#' 不调用 bibliometrix (太慢, 欢迎页加载要秒级), 直接读文件头几行 + 统计 PT/ER.
#' 返回 list 含展示用字段, 文件缺失时 available=FALSE.
#'
#' @return list(available, n_records, topic, year_range, file_size_kb)
demo_data_meta <- function() {
  path <- demo_data_path()
  if (is.null(path)) {
    return(list(available = FALSE, n_records = 0L, topic = "",
                year_range = "", file_size_kb = 0L))
  }
  list(
    available    = TRUE,
    n_records    = length(grep("^PT ", readLines(path, warn = FALSE), value = TRUE)),
    topic        = tools::file_path_sans_ext(basename(path)),
    year_range   = "",
    file_size_kb = as.integer(file.info(path)$size / 1024)
  )
}

#' 一键加载演示数据为 corpus
#'
#' 复用 import_corpus(), 不引入第二条解析路径. 失败时返回 NULL +
#' 通过 warning 暴露错误, 调用方决定如何提示用户.
#'
#' @return data.frame (bibliometrix M) 或 NULL
load_demo_corpus <- function() {
  path <- demo_data_path()
  if (is.null(path)) {
    warning("演示数据文件未随公开仓库提供，请在本地 data/demo/ 放置 WoS plaintext 文件")
    return(NULL)
  }
  tryCatch(
    import_corpus(path, dbsource = "wos", format = "plaintext"),
    error = function(e) {
      warning(sprintf("演示数据解析失败: %s", conditionMessage(e)))
      NULL
    }
  )
}
