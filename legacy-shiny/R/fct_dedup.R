# R/fct_dedup.R — 文献去重与元数据修复
#
# v0.6 spec §3 (N1). 纯函数层, 不依赖 shiny session.
# 两个对外函数:
#   dedup_corpus()    — DOI / 标题模糊去重, 返回去重后 corpus + 去重报告
#   enrich_metadata() — 通过 Crossref 回填缺失字段 (DI/AB), 返回 corpus + 修复报告
#
# 约定:
#   - corpus 为 bibliometrix data.frame, 字段 AU/TI/PY/DI/AB/... (见 fct_openalex_to_corpus.R)
#   - 防御式: M 空/NULL/缺字段时优雅返回, 不崩溃
#   - 网络调用 (Crossref) 必 req_timeout + req_throttle + tryCatch 包裹, 失败降级
#   - 复用 fct_crossref.R 的 httr2 调用风格

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

#' 规范化 DOI: 小写 + 去 https://doi.org/ 等前缀 + 去首尾空白
#'
#' 用于把不同来源的 DOI (有的带 URL 前缀, 大小写不一) 折叠成同一规范形式,
#' 保证 "10.1/X" 与 "https://doi.org/10.1/X" 判为同一篇.
.norm_doi <- function(x) {
  x <- as.character(x %||% "")
  if (length(x) != 1L || is.na(x)) return("")  # NA / 非标量 → 空串 (无 DOI)
  x <- tolower(trimws(x))
  x <- sub("^https?://(dx\\.)?doi\\.org/", "", x, ignore.case = TRUE)
  x <- sub("^doi:\\s*", "", x, ignore.case = TRUE)
  trimws(x)
}

#' 规范化标题: 小写 + 仅保留字母数字 (去标点/空白/HTML 残留)
#'
#' 模糊去重前的归一化, 让 "Foo: A Bar!" 与 "foo a bar" 在相似度计算上等价.
.norm_title <- function(x) {
  x <- as.character(x %||% "")
  if (length(x) != 1L || is.na(x)) return("")  # NA / 非标量 → 空串
  gsub("[^a-z0-9]", "", tolower(x))
}

#' 标题相似度 (0-1), Jaro-Winkler. stringdist 缺失时降级到 base::adist.
#'
#' spec §3.3 指定 stringdist::stringsim(method="jw"). 该包已在 renv.lock.
#' 为稳健 (单测环境/未装包) 提供 adist 编辑距离降级路径.
.title_sim <- function(a, b) {
  if (!nzchar(a) || !nzchar(b)) return(0)
  if (requireNamespace("stringdist", quietly = TRUE)) {
    return(as.numeric(stringdist::stringsim(a, b, method = "jw")))
  }
  d <- utils::adist(a, b)[1, 1]
  1 - d / max(nchar(a), nchar(b))
}

#' 空的去重报告骨架 (列结构固定, 便于下游导出 CSV / 拼 infoBox)
.empty_dedup_report <- function() {
  data.frame(
    row        = integer(0),    # 原始行号
    decision   = character(0),  # kept / removed_dup_doi / removed_dup_title
    matched_to = integer(0),    # 匹配到的保留行号 (kept 行为 NA)
    basis      = character(0),  # 匹配依据: doi / title+year / (kept 行为 "")
    similarity = numeric(0),    # 相似度 (DOI 精确匹配记 1, kept/无匹配记 NA)
    stringsAsFactors = FALSE
  )
}

#' 空的修复报告骨架
.empty_enrich_report <- function() {
  data.frame(
    row    = integer(0),    # 被修复的原始行号
    field  = character(0),  # 补全的字段 (DI / AB / ...)
    source = character(0),  # 数据来源 (crossref)
    value  = character(0),  # 回填值 (摘要可能很长, 这里只存预览)
    stringsAsFactors = FALSE
  )
}

# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------

#' 文献去重
#'
#' 去重维度:
#'   - doi_first: 仅按规范化 DOI 去重 (快). 无 DOI 的行一律保留.
#'   - doi_title (默认): 先 DOI 去重, 再对无 DOI 的行按 标题归一化相似度 +
#'     同年(PY) 模糊去重 (相似度 >= title_threshold 且 PY 相同视为重复).
#' 保留策略: 每组重复保留首次出现 (行号最小) 的那条.
#'
#' @param M data.frame; bibliometrix corpus
#' @param method "doi_first" | "doi_title" (默认)
#' @param title_threshold 标题相似度阈值 (0-1, 默认 0.92)
#' @return list(corpus = 去重后 corpus, report = 去重报告 data.frame)
#'   report 列: row, decision, matched_to, basis, similarity
#' @export
dedup_corpus <- function(M, method = "doi_title", title_threshold = 0.92) {
  # ---- 防御: 空/NULL/非 data.frame ----
  if (is.null(M) || !is.data.frame(M) || nrow(M) == 0L) {
    return(list(corpus = M, report = .empty_dedup_report()))
  }
  method <- match.arg(method, c("doi_title", "doi_first"))
  n <- nrow(M)

  # 缺 DI 列时按全 NA 处理 (无 DOI), 不报错
  doi_raw <- if ("DI" %in% names(M)) M$DI else rep(NA_character_, n)
  doi <- vapply(doi_raw, .norm_doi, character(1), USE.NAMES = FALSE)
  has_doi <- nzchar(doi)

  ti_raw <- if ("TI" %in% names(M)) M$TI else rep(NA_character_, n)
  py_raw <- if ("PY" %in% names(M)) M$PY else rep(NA_integer_, n)

  # decision 默认全部 kept; 命中重复时改写
  decision   <- rep("kept", n)
  matched_to <- rep(NA_integer_, n)
  basis      <- rep("", n)
  similarity <- rep(NA_real_, n)

  # ---- 第一级: DOI 去重 ----
  # 仅对有 DOI 的行分组; 每个规范化 DOI 第一次出现保留, 后续标记 removed_dup_doi.
  seen_doi <- list()  # key=规范DOI, val=保留行号
  for (i in which(has_doi)) {
    key <- doi[i]
    if (is.null(seen_doi[[key]])) {
      seen_doi[[key]] <- i
    } else {
      decision[i]   <- "removed_dup_doi"
      matched_to[i] <- seen_doi[[key]]
      basis[i]      <- "doi"
      similarity[i] <- 1
    }
  }

  # ---- 第二级: 标题 + 同年 模糊去重 (仅 doi_title) ----
  # 范围: 当前仍为 kept 且 无 DOI 的行 (有 DOI 的已在一级处理过).
  if (method == "doi_title") {
    cand <- which(decision == "kept" & !has_doi)
    norm_ti <- vapply(ti_raw, .norm_title, character(1), USE.NAMES = FALSE)
    kept_idx <- integer(0)  # 已确认保留的候选行号 (作为后续比较的基准)
    for (i in cand) {
      ti_i <- norm_ti[i]
      if (!nzchar(ti_i)) {        # 无标题无法模糊匹配, 直接保留
        kept_idx <- c(kept_idx, i)
        next
      }
      matched <- FALSE
      for (j in kept_idx) {
        # 同年 (两者 PY 相等且非 NA) 才比标题, 避免跨年误杀
        same_year <- !is.na(py_raw[i]) && !is.na(py_raw[j]) &&
          py_raw[i] == py_raw[j]
        if (!same_year) next
        sim <- .title_sim(ti_i, norm_ti[j])
        if (sim >= title_threshold) {
          decision[i]   <- "removed_dup_title"
          matched_to[i] <- j
          basis[i]      <- "title+year"
          similarity[i] <- sim
          matched <- TRUE
          break
        }
      }
      if (!matched) kept_idx <- c(kept_idx, i)
    }
  }

  keep_mask <- decision == "kept"
  report <- data.frame(
    row        = seq_len(n),
    decision   = decision,
    matched_to = matched_to,
    basis      = basis,
    similarity = similarity,
    stringsAsFactors = FALSE
  )
  list(corpus = M[keep_mask, , drop = FALSE], report = report)
}

# ---------------------------------------------------------------------------
# 元数据修复
# ---------------------------------------------------------------------------

#' 通过 Crossref 按书目信息 (标题) 反查 DOI
#'
#' 调 Crossref /works?query.bibliographic=<标题>&rows=1, 取首条结果的 DOI.
#' 网络/解析失败一律返回 NA (降级, 不崩溃). 复用 fct_crossref.R 的 throttle 风格.
#'
#' @param title character; 文献标题
#' @param email character; Crossref polite pool 联系邮箱
#' @param timeout_s 超时秒数
#' @return list(doi, abstract) — 任一缺失记 NA_character_
.crossref_by_title <- function(title,
                               email = "aria-review@users.noreply.github.com",
                               timeout_s = 30L) {
  na_out <- list(doi = NA_character_, abstract = NA_character_)
  if (is.null(title) || !nzchar(trimws(title))) return(na_out)

  out <- tryCatch({
    req <- httr2::request("https://api.crossref.org/works")
    req <- httr2::req_url_query(req,
                                query.bibliographic = title,
                                rows = 1L,
                                mailto = email)
    req <- httr2::req_timeout(req, timeout_s)
    req <- httr2::req_throttle(req, rate = 30 / 60)
    req <- httr2::req_user_agent(req, "BiblioCN/0.6")
    req <- httr2::req_retry(req,
                            max_tries = 3,
                            backoff = function(i) 2 ^ i,
                            is_transient = function(resp) {
                              httr2::resp_status(resp) %in%
                                c(429, 500, 502, 503, 504)
                            })
    resp <- httr2::req_perform(req)
    body_raw <- if (is.raw(resp$body)) resp$body
                else charToRaw(as.character(resp$body))
    items <- jsonlite::fromJSON(rawToChar(body_raw),
                                simplifyVector = FALSE)$message$items
    if (!length(items)) return(na_out)
    it <- items[[1]]
    list(
      doi      = it$DOI %||% NA_character_,
      # Crossref 摘要为 JATS XML, 去标签后回填; 缺失记 NA
      abstract = {
        ab <- it$abstract %||% NA_character_
        if (is.character(ab) && nzchar(ab))
          trimws(gsub("\\s+", " ", gsub("<[^>]+>", " ", ab)))
        else NA_character_
      }
    )
  }, error = function(e) {
    warning(sprintf("Crossref 标题反查失败: %s", conditionMessage(e)))
    na_out
  })
  out %||% na_out
}

#' 元数据修复: 回填缺失字段
#'
#' 对 targets 中缺失的字段, 用有标题(TI)的行向 Crossref 反查补全:
#'   - DI: 缺 DOI 但有标题 → Crossref query.bibliographic 反查 DOI
#'   - AB: 缺摘要但有标题 → 取同一次反查结果中的 abstract (若有)
#' 限流保护: 最多查 max_lookup 条; 网络失败逐条降级 (不中断整体).
#'
#' @param M data.frame; bibliometrix corpus
#' @param targets 要补全的字段, 默认 c("DI", "AB") (DI=DOI, AB=abstract)
#' @param max_lookup 最多查多少条 (默认 50)
#' @param lookup_fn 注入点: 按标题反查的函数 (默认 .crossref_by_title),
#'   单测可传 mock 避免真实网络
#' @param email Crossref 联系邮箱
#' @return list(corpus = 修复后 corpus, report = 修复报告 data.frame)
#'   report 列: row, field, source, value
#' @export
enrich_metadata <- function(M, targets = c("DI", "AB"),
                            max_lookup = 50L,
                            lookup_fn = .crossref_by_title,
                            email = "aria-review@users.noreply.github.com") {
  # ---- 防御: 空/NULL/非 data.frame/无标题列 ----
  if (is.null(M) || !is.data.frame(M) || nrow(M) == 0L) {
    return(list(corpus = M, report = .empty_enrich_report()))
  }
  if (!("TI" %in% names(M))) {
    # 无标题无从反查, 原样返回
    return(list(corpus = M, report = .empty_enrich_report()))
  }
  targets <- intersect(targets, c("DI", "AB"))
  if (!length(targets)) {
    return(list(corpus = M, report = .empty_enrich_report()))
  }
  n <- nrow(M)

  # 确保目标列存在 (缺则补 NA 列, 便于统一写入)
  for (f in targets) if (!(f %in% names(M))) M[[f]] <- NA_character_

  #' 判断某行某字段是否"缺失" (NA 或空串)
  is_missing <- function(v) is.na(v) || !nzchar(trimws(as.character(v)))

  # 候选: 任一 target 缺失, 且有标题
  ti <- as.character(M$TI)
  need_row <- logical(n)
  for (i in seq_len(n)) {
    if (is_missing(ti[i])) next
    if (any(vapply(targets, function(f) is_missing(M[[f]][i]), logical(1)))) {
      need_row[i] <- TRUE
    }
  }
  cand <- which(need_row)
  if (length(cand) > max_lookup) cand <- cand[seq_len(max_lookup)]

  report <- .empty_enrich_report()
  for (i in cand) {
    res <- tryCatch(lookup_fn(ti[i], email = email),
                    error = function(e) NULL)
    if (is.null(res)) next

    if ("DI" %in% targets && is_missing(M[["DI"]][i])) {
      d <- res$doi %||% NA_character_
      if (is.character(d) && length(d) == 1L && !is.na(d) && nzchar(d)) {
        M[["DI"]][i] <- d
        report <- rbind(report, data.frame(
          row = i, field = "DI", source = "crossref",
          value = d, stringsAsFactors = FALSE))
      }
    }
    if ("AB" %in% targets && is_missing(M[["AB"]][i])) {
      a <- res$abstract %||% NA_character_
      if (is.character(a) && length(a) == 1L && !is.na(a) && nzchar(a)) {
        M[["AB"]][i] <- a
        report <- rbind(report, data.frame(
          row = i, field = "AB", source = "crossref",
          value = substr(a, 1, 120), stringsAsFactors = FALSE))
      }
    }
  }

  list(corpus = M, report = report)
}

# 文件末尾统一定义 (与 fct_crossref.R 同款语义, source 顺序无关均安全)
`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
