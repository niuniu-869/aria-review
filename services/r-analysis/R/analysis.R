# services/r-analysis/R/analysis.R
# 概览分析, 移植自 legacy R/fct_analysis.R::analyze_overview。
# 关键差异 (Codex step1-P1): 返回与 packages/contracts 的 OverviewResult 对齐的
# 纯 list (仅基本类型), 不返回 bibliometrix/ggplot/igraph 对象 — 跨服务契约,
# 不是 R 对象。"移植逻辑" 与 "定义跨服务契约" 是两件事。

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a

# 从 plumber multipart 字段安全取文本 (字段可能是 list(value=raw) / raw / 字符)
# 修复: 不靠函数签名自动绑定 multipart 字段 (plumber 会传 length-0 覆盖默认值)
.form_text <- function(x, default = NULL) {
  if (is.null(x)) return(default)
  v <- if (is.list(x) && !is.null(x$value)) x$value else x
  if (is.raw(v)) v <- rawToChar(v)
  v <- as.character(v)
  if (!length(v) || !nzchar(v[1])) default else v[1]
}

# 把 ";" 分隔字段拆成去空白、非空的词向量
.split_terms <- function(x, sep = ";") {
  x <- x[!is.na(x)]
  if (!length(x)) return(character(0))
  t <- trimws(unlist(strsplit(as.character(x), sep, fixed = TRUE)))
  t[nzchar(t)]
}

#' 概览 DTO: 从 bibliometrix 语料 M 算出契约 stats + 年度产出
#'
#' @param M data.frame (bibliometrix corpus)
#' @return list(schemaVersion, stats, annualProduction) — jsonlite 可直接序列化
#'   stats 字段对齐 contracts/openapi.yaml OverviewStats。
#'   注: 不含 corpusId/projectId — corpusId 由 plumber /overview 端点注入,
#'   projectId 由 agent 后端补 (Codex step2-P1)。
overview_dto <- function(M) {
  if (!is.data.frame(M) || nrow(M) == 0L) {
    stop("overview_dto: 语料为空或非 data.frame")
  }
  has <- function(col) col %in% names(M)

  documents <- nrow(M)
  sources   <- if (has("SO")) length(unique(.split_terms(M$SO))) else 0L
  authors   <- if (has("AU")) length(unique(.split_terms(M$AU))) else 0L

  tc <- if (has("TC")) suppressWarnings(as.numeric(M$TC)) else NA_real_
  # 分母为全部文档, 缺 TC 记 0 引用 (Codex step2-P1: 否则缺 TC 时均值偏高)
  avg_cit <- round(sum(tc, na.rm = TRUE) / documents, 4)

  py <- if (has("PY")) suppressWarnings(as.integer(M$PY)) else integer(0)
  py <- py[!is.na(py)]
  if (!length(py)) {
    # 契约要求 timespanFrom/To 为必填整数; 无有效出版年则概览无意义。
    stop("overview_dto: 语料缺少有效 PY (出版年), 无法生成概览")
  }
  timespan_from <- min(py)
  timespan_to   <- max(py)

  kw_plus <- if (has("ID")) length(unique(.split_terms(M$ID))) else NA_integer_
  kw_auth <- if (has("DE")) length(unique(.split_terms(M$DE))) else NA_integer_

  # 年度产出 (升序)
  yt <- as.data.frame(table(year = py), stringsAsFactors = FALSE)
  yt$year <- as.integer(as.character(yt$year))
  yt <- yt[order(yt$year), ]
  annual <- unname(Map(
    function(y, n) list(year = as.integer(y), articles = as.integer(n)),
    yt$year, yt$Freq
  ))

  # A4: 语料级 H 指数 (可选) — 文档按被引降序, h = 满足"第 i 篇被引 >= i"的最大 i。
  h_index <- {
    tcv <- if (has("TC")) suppressWarnings(as.numeric(M$TC)) else numeric(0)
    tcv <- tcv[!is.na(tcv)]
    if (!length(tcv)) NULL else {
      s <- sort(tcv, decreasing = TRUE)
      hv <- sum(s >= seq_along(s))
      as.integer(hv)
    }
  }

  # A4: 年均增长率 CAGR (可选, %) — 用首尾年的年度产出。
  # CAGR = ((末年产出/首年产出)^(1/年数) - 1) * 100; 边界(单年/产出<=0)→ NULL。
  annual_growth <- {
    if (nrow(yt) < 2L) NULL else {
      first_n <- as.numeric(yt$Freq[1])
      last_n  <- as.numeric(yt$Freq[nrow(yt)])
      span    <- as.numeric(yt$year[nrow(yt)] - yt$year[1])
      if (first_n > 0 && last_n > 0 && span > 0) {
        round((( last_n / first_n )^(1 / span) - 1) * 100, 1)
      } else NULL
    }
  }

  stats <- list(
    documents          = as.integer(documents),
    sources            = as.integer(sources),
    authors            = as.integer(authors),
    keywordsPlus       = if (is.na(kw_plus)) NULL else as.integer(kw_plus),
    authorKeywords     = if (is.na(kw_auth)) NULL else as.integer(kw_auth),
    avgCitationsPerDoc = avg_cit,
    timespanFrom       = timespan_from,
    timespanTo         = timespan_to,
    hIndex             = h_index,           # 可选: 缺 TC → NULL
    annualGrowthRate   = annual_growth      # 可选: 单年/边界 → NULL
  )
  # 丢掉 NULL 字段 (nullable, 契约允许缺省)
  stats <- stats[!vapply(stats, is.null, logical(1))]

  list(schemaVersion = 1L, stats = stats, annualProduction = annual)
}

#' 语料文献列表 (供 agent 做综述 grounding 上下文)
#' @param M data.frame (bibliometrix corpus)
#' @param limit 返回条数上限
#' @return list of list(idx, title, authors, year, abstract, doi) — NA 字段省略
corpus_records <- function(M, limit = 50L) {
  if (!is.data.frame(M) || nrow(M) == 0L) return(list())
  n <- min(nrow(M), max(1L, as.integer(limit)))
  has <- function(c) c %in% names(M)
  cell <- function(c, i) if (has(c)) M[[c]][i] else NA
  nn <- function(x) {
    if (length(x) == 0L || all(is.na(x)) || !nzchar(trimws(as.character(x)[1]))) NULL
    else as.character(x)[1]
  }
  lapply(seq_len(n), function(i) {
    rec <- list(
      idx      = as.integer(i),
      title    = nn(cell("TI", i)),
      authors  = nn(cell("AU", i)),
      year     = {
        y <- suppressWarnings(as.integer(cell("PY", i)))
        if (length(y) == 0L || is.na(y)) NULL else y
      },
      abstract = nn(cell("AB", i)),
      doi      = nn(cell("DI", i))
    )
    rec[!vapply(rec, is.null, logical(1))]
  })
}
