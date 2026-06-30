# services/r-analysis/R/analysis_advanced.R
# A4 高级图函数 + 统一可用性信封 helper。
#
# 设计要点 (spec §4.0/§4.2/§4.3):
#   - analysis_envelope(): 统一把"惰性计算表达式"包成 {available, ...} 信封。
#     缺字段 → missing_field; 计算抛错 → analysis_error; 行数不足 → not_enough_data;
#     结果空 → computed_empty; 成功 → {available:TRUE, data:...}。
#   - 三个高级图 DTO (作者年度产出 / 关键词历时演变 / 高被引参考文献) 均返回信封。
#   - g/m/tc 与 Bradford rank/cumPct 作为现有 DTO 的"可选字段增量"实现于此 (供 analysis_pages.R 复用)。
#   一律基于 data.frame / 矩阵, 不依赖 plot / htmlwidget 产物。

# `%||%` 已在 analysis.R 定义 (source 顺序保证可用); 此处不重复定义避免遮蔽。

#' 统一可用性信封
#'
#' @param compute   **零参函数** `function() {...}`, 返回 DTO。必须是 function:
#'                  其计算体在 tryCatch 内调用, 抛错才能被捕获为 analysis_error。
#'                  (不收裸表达式: 裸表达式会在进入 tryCatch 前被 promise 强制求值,
#'                   抛错就逃逸了信封语义。)
#' @param required_fields 该图所需的列名向量; 若 df 给定且字段缺/全空 → missing_field。
#' @param min_rows  结果(若为 data.frame/list)最少行/元素数; 不足 → not_enough_data。
#' @param df        用于字段校验的 data.frame (通常是语料 M); 不传则跳过字段校验。
#' @return list: 成功 {available=TRUE, data=...}; 否则 {available=FALSE, reason, message, ...}。
analysis_envelope <- function(compute, required_fields = character(),
                              min_rows = 0L, df = NULL) {
  stopifnot(is.function(compute))
  # 1) 字段校验 (在计算前, 最便宜的失败路径)
  #    "缺列" 与 "列存在但 100% 全空/NA" 同视为 missing_field —
  #    PDF 导入语料常把 CR/DE/SO 建成空列, 若仅查列名会漏判为 analysis_error/
  #    computed_empty, 文案对用户无指导意义 (spec §5: 须明确"缺哪个字段+如何补")。
  if (length(required_fields) && !is.null(df)) {
    miss <- required_fields[!vapply(required_fields,
      function(f) .ae_field_present(df, f), logical(1))]
    if (length(miss)) {
      return(list(
        available = FALSE,
        reason = "missing_field",
        missingField = miss[1],
        message = sprintf("当前语料缺少字段「%s」, 无法生成该图。", miss[1]),
        howto = "PDF 导入语料常缺该字段, 可从 OpenAlex/WoS 导入含该字段的题录。"
      ))
    }
  }

  # 2) 计算体在 tryCatch 内调用 → 抛错可捕获为 analysis_error
  out <- tryCatch(compute(), error = function(e) {
    structure(list(message = conditionMessage(e)), class = "ae_error")
  })
  if (inherits(out, "ae_error")) {
    return(list(
      available = FALSE,
      reason = "analysis_error",
      message = "分析计算出错, 已捕获。",
      detail = out$message
    ))
  }

  # 3) 行数 / 空判定
  n <- .ae_count(out)
  if (n == 0L) {
    return(list(
      available = FALSE,
      reason = "computed_empty",
      message = "计算成功但结果为空 (无符合条件的数据)。"
    ))
  }
  if (min_rows > 0L && n < min_rows) {
    return(list(
      available = FALSE,
      reason = "not_enough_data",
      message = sprintf("数据样本不足 (需至少 %d, 实得 %d)。", min_rows, n)
    ))
  }

  list(available = TRUE, data = out)
}

# 字段"实质存在": 列在 colnames 且至少有一个非空(非 NA 且去空白后非"")值。
# 用于把 PDF 语料的全空 CR/DE/SO 列正确判为缺字段。
.ae_field_present <- function(df, f) {
  if (!f %in% colnames(df)) return(FALSE)
  v <- df[[f]]
  any(!is.na(v) & nzchar(trimws(as.character(v))))
}

# 统计结果"行数/元素数": data.frame 用 nrow; list 用 length; 其它用 length。
.ae_count <- function(out) {
  if (is.null(out)) return(0L)
  if (is.data.frame(out)) return(nrow(out))
  if (is.list(out)) {
    # DTO 形如 {authors, years, cells} → 以 cells 长度为准 (cells 是核心数据)
    if (!is.null(out$cells)) return(length(out$cells))
    return(length(out))
  }
  length(out)
}

# ---------------------------------------------------------------------------
# 作者年度产出时间线 (热力图: 作者 × 年份)
# bibliometrix::authorProdOverTime(M, k)$dfAU → cols Author/year/freq/TC/TCpY
# 需 PY (出版年); 缺 → missing_field。
# ---------------------------------------------------------------------------
author_production_dto <- function(M, k = 10L) {
  analysis_envelope(function() {
    k <- max(1L, as.integer(k))
    ap <- bibliometrix::authorProdOverTime(M, k = k, graph = FALSE)
    df <- ap$dfAU
    if (is.null(df) || !nrow(df)) return(list(authors = list(), years = list(), cells = list()))
    df$Author <- as.character(df$Author)
    df$year   <- suppressWarnings(as.integer(df$year))
    df$freq   <- suppressWarnings(as.integer(df$freq))
    df <- df[!is.na(df$year) & !is.na(df$freq), , drop = FALSE]
    if (!nrow(df)) return(list(authors = list(), years = list(), cells = list()))
    # 作者按总发文降序 (k 已限定 top-k); 年份升序
    au_order <- names(sort(tapply(df$freq, df$Author, sum), decreasing = TRUE))
    yrs <- sort(unique(df$year))
    cells <- unname(Map(function(a, y, f)
      list(author = as.character(a), year = as.integer(y), articles = as.integer(f)),
      df$Author, df$year, df$freq))
    list(
      authors = as.list(au_order),
      years   = as.list(as.integer(yrs)),
      cells   = cells
    )
  }, required_fields = c("PY", "AU"), df = M)
}

# ---------------------------------------------------------------------------
# 关键词历时演变 (themeRiver / 堆叠面积)
# 按年聚合 DE 频次, 取 top 关键词 (默认 top15) 避免过宽。
# 需 DE + PY; 缺任一 → missing_field。
# ---------------------------------------------------------------------------
keyword_trend_dto <- function(M, top_terms = 15L) {
  analysis_envelope(function() {
    top_terms <- max(1L, as.integer(top_terms))
    de <- as.character(M$DE)
    py <- suppressWarnings(as.integer(M$PY))
    keep <- !is.na(py) & nzchar(trimws(de))
    de <- de[keep]; py <- py[keep]
    if (!length(de)) return(list(years = list(), terms = list(), cells = list()))
    # 逐文档拆分 DE (";"), 与该文档年份配对, 展平为 (year, term)
    rows <- list()
    for (i in seq_along(de)) {
      terms <- trimws(unlist(strsplit(de[i], ";", fixed = TRUE)))
      terms <- terms[nzchar(terms)]
      if (!length(terms)) next
      rows[[length(rows) + 1L]] <- data.frame(
        year = py[i], term = toupper(terms),
        stringsAsFactors = FALSE)
    }
    if (!length(rows)) return(list(years = list(), terms = list(), cells = list()))
    flat <- do.call(rbind, rows)
    # 全局 top 关键词 (按总频次)
    tot <- sort(table(flat$term), decreasing = TRUE)
    top <- names(utils::head(tot, top_terms))
    flat <- flat[flat$term %in% top, , drop = FALSE]
    if (!nrow(flat)) return(list(years = list(), terms = list(), cells = list()))
    agg <- as.data.frame(table(year = flat$year, term = flat$term),
                         stringsAsFactors = FALSE)
    agg <- agg[agg$Freq > 0, , drop = FALSE]
    agg$year <- as.integer(as.character(agg$year))
    yrs <- sort(unique(agg$year))
    cells <- unname(Map(function(y, t, f)
      list(year = as.integer(y), term = as.character(t), freq = as.integer(f)),
      agg$year, agg$term, agg$Freq))
    list(
      years = as.list(as.integer(yrs)),
      terms = as.list(top),  # 已按全局总频次降序
      cells = cells
    )
  }, required_fields = c("DE", "PY"), df = M)
}

# ---------------------------------------------------------------------------
# 高被引参考文献表 (DataTable: 参考文献 | 次数)
# bibliometrix::citations(M, field="article")$Cited → 命名向量 (names=参考文献, 值=次数)
# 需 CR; 缺 → missing_field。取 top20。
# ---------------------------------------------------------------------------
cited_refs_dto <- function(M, top = 20L) {
  analysis_envelope(function() {
    top <- max(1L, as.integer(top))
    ci <- bibliometrix::citations(M, field = "article", sep = ";")
    cited <- ci$Cited
    if (is.null(cited) || !length(cited)) return(list())
    cited <- utils::head(cited, top)
    refs <- names(cited)
    unname(Map(function(r, n)
      list(ref = as.character(r), count = as.integer(n)),
      refs, as.integer(cited)))
  }, required_fields = "CR", df = M)
}

# ---------------------------------------------------------------------------
# g / m / tc 增量 (供 sources_dto / authors_dto 复用)
# 复用 bibliometrix::Hindex(M, field)$H, 含 h_index/g_index/m_index/TC。
# m 边界: m_index 为 NaN/Inf/NA (首发年缺失或年数<=0) → NULL (JSON null)。
# 返回以 Element (来源/作者名) 为键的 list, 值为 list(g, m, tc)。
# ---------------------------------------------------------------------------
hindex_gmt_map <- function(M, field) {
  out <- tryCatch({
    H <- bibliometrix::Hindex(M, field = field, elements = NULL,
                              sep = ";", years = Inf)$H
    res <- list()
    for (i in seq_len(nrow(H))) {
      el <- as.character(H$Element[i])
      m  <- suppressWarnings(as.numeric(H$m_index[i]))
      # m 边界: 非有限 (NaN/Inf) 或 NA → null
      m_val <- if (length(m) && is.finite(m)) round(m, 4) else NULL
      res[[el]] <- list(
        g  = as.integer(H$g_index[i]),
        m  = m_val,
        tc = as.integer(H$TC[i])
      )
    }
    res
  }, error = function(e) list())
  out
}
