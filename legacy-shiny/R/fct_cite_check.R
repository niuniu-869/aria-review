# R/fct_cite_check.R — AI 输出引用完整性校验 (抗幻觉)
#
# 设计原则 (spec §6):
#   · 纯函数, 不依赖 shiny session — 方便单测与复用
#   · 提取 AI 输出 markdown 中的引用, 逐条比对当前 corpus
#   · 三色判定: green(✅ 精确命中) / yellow(⚠️ 模糊命中) / red(❌ 疑似虚构)
#   · annotated 输出在每条引用后内嵌 emoji marker, 仍是合法 markdown,
#     直接喂给 render_markdown_safe() — emoji 为 Unicode 字符默认通过 sanitizer
#
# corpus 字段约定 (见 fct_openalex_to_corpus.R / fct_cite.R):
#   · TI 标题 (大写) · AU 作者 "LASTNAME FI;LASTNAME2 FI2" (大写, 分号分隔)
#   · PY 年份 (integer/character) · DI 裸 DOI (无 https 前缀) · AB 摘要
#   · 可选 PM 字段 = PMID (PubMed 接入后存在)
#
# 性能: spec 要求 <200ms/篇(50 引用). 正则一次性提取; 匹配阶段先把
#   corpus 的 DOI/作者姓 normalize 成查找索引, 避免逐引用重复全表扫描.

# ---- emoji marker 常量 -----------------------------------------------------

CITE_MARK <- list(green = "✅", yellow = "⚠️", red = "❌")  # ✅ ⚠️ ❌

# ---- 内部: 文本归一化 ------------------------------------------------------

#' DOI 归一化: 去 https 前缀 / 小写 / 去尾随标点
.cc_norm_doi <- function(x) {
  x <- tolower(trimws(x %||% ""))
  x <- sub("^https?://(dx\\.)?doi\\.org/", "", x)
  x <- sub("^doi:\\s*", "", x)
  sub("[\\.,;:)\\]。，]+$", "", x, perl = TRUE)  # 去尾随中英标点
}

#' 作者姓归一化: 小写, 仅保留字母/CJK (去标点空白) — 用于模糊匹配
.cc_norm_name <- function(x) {
  x <- tolower(x %||% "")
  gsub("[^a-z一-鿿]", "", x, perl = TRUE)
}

# ---- 内部: corpus → 查找索引 -----------------------------------------------

#' 把 corpus 预处理成匹配用的索引 (一次性, 避免逐引用重复扫描)
#'
#' @return list(
#'   doi   = 归一化 DOI 向量 (与行号同序, 无值为 ""),
#'   pmid  = PMID 向量 (字符, 无值为 ""),
#'   title = 小写标题向量,
#'   surnames = list(每行的作者姓集合, 已归一化),
#'   year  = 整数年份向量
#' )
.cc_build_index <- function(corpus) {
  n <- if (is.null(corpus)) 0L else nrow(corpus)
  empty <- list(doi = character(0), pmid = character(0), title = character(0),
                surnames = list(), year = integer(0), n = 0L)
  if (n == 0L) return(empty)

  col <- function(name) {
    if (name %in% names(corpus)) as.character(corpus[[name]]) else rep("", n)
  }

  doi  <- vapply(col("DI"), .cc_norm_doi, character(1), USE.NAMES = FALSE)
  pmid <- trimws(col("PM"))
  ti   <- tolower(trimws(col("TI")))
  py_raw <- col("PY")
  year <- suppressWarnings(as.integer(sub("^.*?(\\d{4}).*$", "\\1", py_raw)))

  au_raw <- col("AU")
  surnames <- lapply(au_raw, function(s) {
    if (is.na(s) || !nzchar(s)) return(character(0))
    # "ARIA M;CUCCURULLO C" → 每条取第一个 token (姓), 归一化
    authors <- strsplit(s, ";", fixed = TRUE)[[1]]
    fam <- vapply(authors, function(a) {
      a <- trimws(a)
      tok <- strsplit(a, "[ ,]+", perl = TRUE)[[1]]
      tok <- tok[nzchar(tok)]
      if (length(tok)) tok[1] else ""
    }, character(1), USE.NAMES = FALSE)
    unique(.cc_norm_name(fam[nzchar(fam)]))
  })

  list(doi = doi, pmid = pmid, title = ti, surnames = surnames,
       year = year, n = n)
}

# ---- 内部: 引用提取 --------------------------------------------------------

#' 从 ai_text 提取所有引用, 返回 data.frame(text, type, start, end)
#'
#' 覆盖 spec §6.4 的 6 种模式 (第 6 题名片段做轻量版).
#' start/end 为字符位置 (用于 annotated 阶段在原文精确插入 marker).
.cc_extract <- function(ai_text) {
  empty <- data.frame(text = character(0), type = character(0),
                      start = integer(0), end = integer(0),
                      stringsAsFactors = FALSE)
  if (is.null(ai_text) || length(ai_text) == 0L) return(empty)
  ai_text <- paste(ai_text, collapse = "\n")
  if (!nzchar(ai_text)) return(empty)

  # 各模式正则 (perl). 顺序 = 优先级: 先 DOI/PMID (强信号), 再作者+年, 最后编号.
  patterns <- list(
    # 1. DOI: 10.xxxx/yyyy (排除尾随空白/逗号/右括号/方括号)
    doi  = "10\\.\\d{4,}/[^\\s,;)\\]。，）]+",
    # 5. PMID: "PMID: 123" 或 "PMID 123"
    pmid = "PMID[:\\s]+\\d+",
    # 3. 作者+年(中): 张三 (2023) / （李四, 2021） / 王五等 (2020)
    cn   = "[一-鿿]{2,4}(?:等)?\\s*[（(]\\s*\\d{4}\\s*[）)]|[（(]\\s*[一-鿿]{2,4}\\s*[,，]\\s*\\d{4}\\s*[）)]",
    # 2. 作者+年(英): Smith et al. (2023) / Smith and Jones (2021) / (Smith, 2023)
    #    第二段 (?:et al.|and Name|& Name) 可选: "et al." 独立成段 (不带后续姓);
    #    "and"/"&" 必带后续姓.
    en   = "[A-Z][A-Za-zÀ-ɏ'’-]+(?:\\s+et al\\.?|\\s+(?:and|&)\\s+[A-Z][A-Za-zÀ-ɏ'’-]+)?\\s*\\(\\s*\\d{4}[a-z]?\\s*\\)|\\(\\s*[A-Z][A-Za-zÀ-ɏ'’-]+(?:\\s+et al\\.?|\\s+(?:and|&)\\s+[A-Z][A-Za-zÀ-ɏ'’-]+)?\\s*,\\s*\\d{4}[a-z]?\\s*\\)",
    # 4. 编号: [1] [12]
    num  = "\\[\\d{1,3}\\]"
  )

  hits <- list()
  for (ty in names(patterns)) {
    m <- gregexpr(patterns[[ty]], ai_text, perl = TRUE)[[1]]
    if (length(m) == 1L && m[1] == -1L) next
    lens <- attr(m, "match.length")
    for (k in seq_along(m)) {
      st <- m[k]; en <- st + lens[k] - 1L
      hits[[length(hits) + 1L]] <- data.frame(
        text  = substr(ai_text, st, en),
        type  = ty, start = st, end = en,
        stringsAsFactors = FALSE)
    }
  }
  if (!length(hits)) return(empty)
  df <- do.call(rbind, hits)

  # 去重叠: 同一区间被多模式命中时, 保留最早出现+最长 (DOI/PMID 强信号优先).
  # 排序: start 升序, 然后长度降序.
  df <- df[order(df$start, -(df$end - df$start)), , drop = FALSE]
  keep <- rep(TRUE, nrow(df))
  last_end <- 0L
  for (i in seq_len(nrow(df))) {
    if (df$start[i] <= last_end) { keep[i] <- FALSE; next }
    last_end <- df$end[i]
  }
  df <- df[keep, , drop = FALSE]
  rownames(df) <- NULL
  df
}

# ---- 内部: 单条引用判定 ----------------------------------------------------

#' 解析作者+年引用文本 → list(surname=归一化首姓, year=整数)
.cc_parse_author_year <- function(text, type) {
  yr <- suppressWarnings(as.integer(sub("^.*?(\\d{4}).*$", "\\1", text)))
  # 取第一个 "姓" token: 英文取首个大写词; 中文取开头 2-4 汉字
  if (type == "cn") {
    sur <- regmatches(text, regexpr("[一-鿿]{2,4}", text, perl = TRUE))
  } else {
    sur <- regmatches(text, regexpr("[A-Za-zÀ-ɏ'’-]{2,}", text, perl = TRUE))
  }
  sur <- if (length(sur)) sur[1] else ""
  # 去中文 "等" 尾缀
  sur <- sub("等$", "", sur)
  list(surname = .cc_norm_name(sur), year = yr)
}

#' 判定单条引用 status + matched_idx
#'
#' @return list(status, matched_idx)  matched_idx 为 corpus 行号 (NA 表示无)
.cc_judge <- function(text, type, idx) {
  no_match <- list(status = "red", matched_idx = NA_integer_)
  if (idx$n == 0L) return(no_match)

  if (type == "doi") {
    key <- .cc_norm_doi(text)
    hit <- which(nzchar(idx$doi) & idx$doi == key)
    if (length(hit)) return(list(status = "green", matched_idx = hit[1]))
    return(no_match)
  }

  if (type == "pmid") {
    key <- sub("^PMID[:\\s]+", "", text, perl = TRUE)
    key <- trimws(key)
    hit <- which(nzchar(idx$pmid) & idx$pmid == key)
    if (length(hit)) return(list(status = "green", matched_idx = hit[1]))
    return(no_match)
  }

  if (type %in% c("en", "cn")) {
    ay <- .cc_parse_author_year(text, type)
    if (!nzchar(ay$surname)) return(no_match)
    # 模糊匹配: 该姓出现在某行作者集合 且 (年份匹配 或 引用无年份)
    cand <- vapply(seq_len(idx$n), function(i) {
      ay$surname %in% idx$surnames[[i]]
    }, logical(1))
    if (any(cand) && !is.na(ay$year)) {
      both <- cand & !is.na(idx$year) & idx$year == ay$year
      if (any(both)) return(list(status = "yellow", matched_idx = which(both)[1]))
    }
    # 仅姓命中 (年份不符或缺) 仍算 yellow — 提示"请确认", 不直接判虚构
    if (any(cand)) return(list(status = "yellow", matched_idx = which(cand)[1]))
    return(no_match)
  }

  if (type == "num") {
    # 编号引用 [n]: 无法独立验证真实性, 标 yellow 提示用户核对编号表
    return(list(status = "yellow", matched_idx = NA_integer_))
  }

  # type == "title" (TODO: 题名片段精确包含 → green) — 见 .cc_extract 暂未启用
  no_match
}

# ---- 内部: 生成 annotated markdown -----------------------------------------

#' 在原文每条引用后插入 emoji marker (从后往前插, 不破坏前面位置)
.cc_annotate <- function(ai_text, cites) {
  ai_text <- paste(ai_text, collapse = "\n")
  if (!nrow(cites)) return(ai_text)
  ord <- order(cites$end, decreasing = TRUE)  # 从后往前
  out <- ai_text
  for (i in ord) {
    mk <- CITE_MARK[[cites$status[i]]]
    en <- cites$end[i]
    out <- paste0(substr(out, 1L, en), " ", mk, substr(out, en + 1L, nchar(out)))
  }
  out
}

# ---- 公共入口 --------------------------------------------------------------

#' 校验 AI 输出中的引用是否真实存在于 corpus
#'
#' 提取 AI markdown 中的 6 类引用模式, 逐条比对当前 corpus, 三色判定:
#'   · green  (✅): DOI/PMID 精确命中 corpus 的 DI/PM 字段
#'   · yellow (⚠️): 作者+年模糊命中 (姓在 AU 且年份匹配 PY); 编号引用待核
#'   · red    (❌): corpus 内无任何匹配 → 疑似 AI 虚构
#' annotated 输出在每条引用后内嵌 emoji, 仍是合法 markdown, 可直接喂给
#' render_markdown_safe() (emoji 为 Unicode 默认通过 sanitizer).
#'
#' @param ai_text character; AI 输出原文 (markdown, 可空/可向量)
#' @param corpus  data.frame; 当前 bibliometrix corpus (可空/可缺字段)
#' @return list(
#'   cites   = data.frame(text, type, status, matched_idx),  # 每条引用一行
#'   annotated = character(1),                               # 带行内 marker 的 markdown
#'   summary = list(green = N, yellow = N, red = N)
#' )
#' @export
check_citations <- function(ai_text, corpus) {
  empty_cites <- data.frame(text = character(0), type = character(0),
                            status = character(0), matched_idx = integer(0),
                            stringsAsFactors = FALSE)
  empty_summary <- list(green = 0L, yellow = 0L, red = 0L)

  # 防御: 空文本 → 空结果不崩
  if (is.null(ai_text) || length(ai_text) == 0L) {
    return(list(cites = empty_cites, annotated = "", summary = empty_summary))
  }
  flat <- paste(ai_text, collapse = "\n")
  if (is.na(flat) || !nzchar(flat)) {
    return(list(cites = empty_cites, annotated = "", summary = empty_summary))
  }

  idx <- .cc_build_index(corpus)          # corpus 空 / 缺字段 → 索引为空, 全判 red
  raw <- .cc_extract(flat)

  if (!nrow(raw)) {
    # 无引用: annotated 即原文
    return(list(cites = empty_cites, annotated = flat, summary = empty_summary))
  }

  status <- character(nrow(raw))
  matched <- integer(nrow(raw))
  for (i in seq_len(nrow(raw))) {
    j <- .cc_judge(raw$text[i], raw$type[i], idx)
    status[i] <- j$status
    matched[i] <- j$matched_idx
  }
  raw$status <- status
  raw$matched_idx <- matched

  annotated <- .cc_annotate(flat, raw)

  cites <- raw[, c("text", "type", "status", "matched_idx"), drop = FALSE]
  rownames(cites) <- NULL

  summary <- list(
    green  = sum(cites$status == "green"),
    yellow = sum(cites$status == "yellow"),
    red    = sum(cites$status == "red")
  )

  list(cites = cites, annotated = annotated, summary = summary)
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
