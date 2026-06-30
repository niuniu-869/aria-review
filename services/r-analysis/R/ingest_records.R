# ingest_records.R — 结构化题录 → bibliometrix 数据框（保真路径，不绕 OpenAlex）
#
# 接收来自 BiblioCN Postgres 的 paper 题录数组（JSON），映射为 bibliometrix
# convert2df 产出的 WoS/OpenAlex 行格式（AU/TI/SO/PY/DI/AB/DE/DT 等列），
# 再走 save_corpus() 落盘。
#
# 设计原则：
#   - 不调用任何外部 API，完全保真（论文内容来自 Postgres 快照）。
#   - 列命名与 bibliometrix 内部字段一致，使分析函数无需修改。
#   - 极小样本（< 5 篇）部分分析指标无意义但不应报错；bibliometrix
#     biblioshiny 同样对小样本有降级处理，这里保持一致。
#   - plumber 的 jsonlite 解析默认 simplifyVector=TRUE，会把 JSON 数组转成
#     data.frame，而嵌套的 creators/csl_json 变成 list 列（list-of-df）。
#     .normalize_record() 统一做结构归一，后续代码只处理 list-of-lists。

# ---- 辅助: 从 plumber/jsonlite 简化结构里还原 list-of-lists ----
# plumber simplifyVector=TRUE 时, records 可能是:
#   - data.frame: 行是一条 record, 嵌套字段(creators/csl_json)在 list 列
#   - list: 每个元素是命名 list (已是 list-of-list 格式)
# 本函数把一行 data.frame 还原成命名 list, 并展开单元素包裹的嵌套字段。
.normalize_record <- function(rec) {
  # 若 rec 是 data.frame 行（as.list 后每字段仍保留外层 list 包裹）
  # 则 rec[["creators"]] 形如 list(data.frame(...)) 或 list(list(...))
  if (!is.list(rec)) rec <- as.list(rec)

  # 展开 creators: list[[1]] 可能是 data.frame 或 list-of-list
  cr_raw <- rec[["creators"]]
  if (!is.null(cr_raw)) {
    # 外层 length-1 list 包裹（as.list(df_row) 的副作用）
    if (is.list(cr_raw) && length(cr_raw) == 1 && (is.data.frame(cr_raw[[1]]) || is.list(cr_raw[[1]]))) {
      cr_raw <- cr_raw[[1]]
    }
    # data.frame → list-of-named-lists
    if (is.data.frame(cr_raw)) {
      cr_raw <- lapply(seq_len(nrow(cr_raw)), function(i) as.list(cr_raw[i, , drop = FALSE]))
    }
    rec[["creators"]] <- cr_raw
  }

  # 展开 csl_json: 同理，可能是 list(list(...)) 或直接 list(...)
  csl_raw <- rec[["csl_json"]]
  if (!is.null(csl_raw)) {
    csl_names <- names(csl_raw)
    is_wrapped_csl <- is.null(csl_names) || !length(csl_names) || !nzchar(csl_names[[1]])
    if (is.list(csl_raw) && length(csl_raw) == 1 && is.list(csl_raw[[1]]) && is_wrapped_csl) {
      csl_raw <- csl_raw[[1]]
    }
    if (is.data.frame(csl_raw)) {
      csl_raw <- as.list(csl_raw[1, , drop = FALSE])
    }
    rec[["csl_json"]] <- csl_raw
  }

  rec
}

# ---- 辅助: 把 creators list-of-lists → "LASTNAME FI; ..." (bibliometrix AU 列) ----
.rec_creators_to_au <- function(creators) {
  if (is.null(creators) || !length(creators)) return(NA_character_)
  if (is.character(creators)) {
    parts <- toupper(trimws(creators[nzchar(trimws(creators))]))
    if (!length(parts)) return(NA_character_)
    return(paste(parts, collapse = ";"))
  }
  parts <- character(0)
  for (cr in creators) {
    if (is.character(cr) && length(cr) >= 1L) {
      lit <- trimws(cr[[1]])
      if (nzchar(lit)) parts <- c(parts, toupper(lit))
      next
    }
    if (!is.list(cr)) next
    # CSL-JSON 格式: {family, given} 或 {literal}
    family <- cr$family %||% cr$lastName %||% ""
    given  <- cr$given  %||% cr$firstName %||% ""
    if (!nzchar(trimws(family %||% "")) && !nzchar(trimws(given %||% ""))) {
      lit <- cr$literal %||% cr$name %||% ""
      if (nzchar(trimws(lit %||% ""))) parts <- c(parts, toupper(trimws(lit)))
      next
    }
    last   <- toupper(trimws(family %||% ""))
    given_clean <- trimws(given %||% "")
    inits  <- if (nzchar(given_clean)) {
      toupper(paste0(substr(strsplit(given_clean, "\\s+")[[1]], 1, 1), collapse = ""))
    } else ""
    if (nzchar(inits)) parts <- c(parts, paste(last, inits))
    else               parts <- c(parts, last)
  }
  if (!length(parts)) return(NA_character_)
  paste(parts, collapse = ";")
}

# ---- 辅助: 安全取字符串字段（data.frame 列取 [1] 避免 character(0) 陷阱）----
.scalar_str <- function(x, default = "") {
  if (is.null(x) || length(x) == 0) return(default)
  v <- x[[1]]
  if (is.null(v) || is.na(v)) return(default)
  trimws(as.character(v))
}

# ---- 辅助: 安全取整数字段 ----
.scalar_int <- function(x, default = NA_integer_) {
  if (is.null(x) || length(x) == 0) return(default)
  v <- suppressWarnings(as.integer(x[[1]]))
  if (is.na(v)) default else v
}

# ---- 辅助: references → bibliometrix CR 列 (引用/智力结构分析) ----
# 字符串(分号分隔) / list-of-refs(citation/raw/unstructured/doi/title) / data.frame 均可,
# 去空去重后大写分号串。无引用返回 NA。
.refs_to_cr <- function(x) {
  if (is.null(x) || length(x) == 0) return(NA_character_)
  if (is.data.frame(x)) x <- lapply(seq_len(nrow(x)), function(i) as.list(x[i, , drop = FALSE]))
  if (is.character(x)) {
    refs <- trimws(unlist(strsplit(paste(x, collapse = ";"), ";", fixed = TRUE)))
  } else if (is.list(x)) {
    refs <- vapply(x, function(ref) {
      if (is.character(ref)) return(trimws(ref[[1]]))
      if (!is.list(ref)) return("")
      trimws(as.character(
        ref$citation %||% ref$raw %||% ref$unstructured %||%
          ref$id %||% ref$doi %||% ref$title %||% ""
      ))
    }, character(1), USE.NAMES = FALSE)
  } else {
    refs <- character(0)
  }
  refs <- unique(refs[nzchar(refs)])
  if (!length(refs)) return(NA_character_)
  toupper(paste(refs, collapse = "; "))
}

# ---- 辅助: 关键词标准化 → 统一为分号分隔 DE 字段 ----
# bibliometrix 全链路(词频/历时演变/共现网络/Sankey)均以 ";" 切分 DE，但上游
# 关键词来源分隔符不统一(OpenAlex 分号、PDF/手动/Sciverse 常逗号、LLM 可能返回列表)。
# 若不归一，逗号分隔串会整体塌缩成"一个关键词"，列表只会保留首项——这正是
# "R 分析做不出关键词"的根因。本函数把任意形态归一为去空、去重、大写的分号串：
#   - 列表/向量: 逐元素展开(修复历史 .scalar_str 仅取首项导致整库关键词丢失)
#   - 字符串: 分号优先(文献计量标准); 无分号时按逗号/竖线/中文顿号/中文分号切分
# 无有效关键词时返回 ""。
.normalize_keywords <- function(x) {
  if (is.null(x)) return("")
  v <- unlist(x, use.names = FALSE)
  v <- v[!is.na(v)]
  if (!length(v)) return("")
  v <- as.character(v)
  split_one <- function(s) {
    if (grepl(";", s, fixed = TRUE))      strsplit(s, ";", fixed = TRUE)[[1]]
    else if (grepl("[,，、|；]", s))       strsplit(s, "[,，、|；]")[[1]]
    else                                   s
  }
  terms <- if (length(v) == 1L) split_one(v) else unlist(lapply(v, split_one), use.names = FALSE)
  terms <- toupper(trimws(terms))
  terms <- terms[nzchar(terms)]
  if (!length(terms)) return("")
  paste(unique(terms), collapse = ";")
}

# ---- 辅助: 生成 bibliometrix 短引用 SR 列 (首作者, 年份, 来源) 并保证唯一 ----
# 多数 bibliometrix 分析函数(tableTag/biblioNetwork 等)以 SR 为文档主键, 且会先
# `M[!duplicated(M$SR), ]`。SR 缺失或非唯一都会导致分析落空, 故此处生成并去重。
.make_sr <- function(df) {
  first_au <- sub(";.*", "", as.character(df$AU))   # 首作者 token "SURNAME FI"
  first_au[is.na(first_au) | !nzchar(trimws(first_au))] <- "ANONYMOUS"
  py <- ifelse(is.na(df$PY), "NA", as.character(df$PY))
  so <- as.character(df$SO)
  so[is.na(so) | !nzchar(trimws(so))] <- "NO SOURCE"
  base <- toupper(trimws(paste(first_au, py, so, sep = ", ")))
  make.unique(base, sep = " -")                     # 重复短引用追加 " -1"/" -2" 保证唯一
}

# ---- 辅助: 单 paper record（已 normalized list）→ bibliometrix 格式命名列 list ----
.record_to_bib_row <- function(rec) {
  rec <- .normalize_record(rec)

  # 字段来源：paper 表直接列 + csl_json 后备
  csl  <- rec$csl_json %||% list()
  if (!is.list(csl)) csl <- list()

  title    <- toupper(.scalar_str(rec$title   %||% csl$title))
  year     <- .scalar_int(rec$year %||% csl$issued$`date-parts`[[1]][[1]])
  doi_raw  <- .scalar_str(rec$doi %||% csl$DOI)
  doi      <- sub("^https?://(dx\\.)?doi\\.org/", "", doi_raw, ignore.case = TRUE)
  abstract <- toupper(.scalar_str(rec$abstract %||% csl$abstract))
  so       <- toupper(.scalar_str(rec$container_title %||% csl$`container-title`))
  volume   <- .scalar_str(rec$volume %||% csl$volume)
  issue    <- .scalar_str(rec$issue  %||% csl$issue)
  pages    <- .scalar_str(rec$pages  %||% csl$page)
  bp       <- sub("-.*", "", pages)
  ep       <- sub(".*-", "", pages)
  if (identical(bp, ep) || !nzchar(pages)) ep <- ""

  # 关键词: keywords 字段（列表/分号/逗号分隔）或 csl_json subjects；统一归一为分号串
  de <- .normalize_keywords(rec$keywords)
  if (!nzchar(de)) de <- .normalize_keywords(csl$subject)

  # 引用: references → CR 列（bibliometrix 智力结构/引用分析）
  cr <- .refs_to_cr(rec$references %||% csl$references %||% csl$reference)

  # 作者: 优先 creators 列（JSON），后备 csl_json author
  creators <- if (!is.null(rec$creators) && length(rec$creators) > 0) rec$creators
              else if (!is.null(csl$author) && length(csl$author) > 0)  csl$author
              else                                                         list()
  # 处理 csl$author 也可能是 data.frame 的情况
  if (is.data.frame(creators)) {
    creators <- lapply(seq_len(nrow(creators)), function(i) as.list(creators[i, , drop = FALSE]))
  }
  au <- .rec_creators_to_au(creators)

  # 语言
  la <- toupper(.scalar_str(rec$language %||% csl$language))
  if (!nzchar(la)) la <- "ENGLISH"

  # 来源标识符: 优先 DOI，否则用 title hash
  ut <- if (nzchar(doi)) paste0("DOI:", toupper(doi))
        else paste0("TIT:", substr(digest::digest(title, algo = "md5"), 1, 16))

  # 组装行 (bibliometrix 识别的标准列名)
  list(
    AU = au,
    TI = if (nzchar(title)) title else NA_character_,
    SO = if (nzchar(so)) so else NA_character_,
    PY = year,
    DI = if (nzchar(doi)) doi else NA_character_,
    AB = if (nzchar(abstract)) abstract else NA_character_,
    DE = if (nzchar(de)) de else NA_character_,
    CR = cr,
    VL = if (nzchar(volume)) volume else NA_character_,
    IS = if (nzchar(issue)) issue else NA_character_,
    BP = if (nzchar(bp)) bp else NA_character_,
    EP = if (nzchar(ep)) ep else NA_character_,
    DT = "ARTICLE",
    LA = la,
    UT = ut,
    DB = "BIBLIOCN"
  )
}

# ---- 主入口: records list/df → bibliometrix data.frame ----
#' @param records list-of-lists 或 data.frame（来自 JSON 解码，支持 plumber simplifyVector）
#' @return data.frame with bibliometrix column names, nrow >= 1
records_to_bib_df <- function(records) {
  if (is.null(records) || length(records) == 0) stop("records 为空，无法构建语料")

  # plumber simplifyVector=TRUE 时，JSON 数组 → data.frame
  # 每行仍需经 .normalize_record 处理嵌套字段
  if (is.data.frame(records)) {
    record_list <- lapply(seq_len(nrow(records)), function(i) {
      as.list(records[i, , drop = FALSE])
    })
  } else {
    record_list <- records
  }

  rows <- lapply(record_list, .record_to_bib_row)
  df   <- do.call(rbind.data.frame, c(rows, list(stringsAsFactors = FALSE, make.row.names = FALSE)))

  # bibliometrix 期望 PY 为 numeric（integer 也可）
  df$PY <- suppressWarnings(as.integer(df$PY))

  # 确保 TC（被引数）列存在（分析函数需要，来自 records 可能缺失）
  if (!"TC" %in% colnames(df)) df$TC <- 0L
  if (!"CR" %in% colnames(df)) df$CR <- NA_character_

  # 去重：同一 UT 只保留第一行（corpus_paper 快照幂等，但防御性去重）
  df <- df[!duplicated(df$UT), , drop = FALSE]
  if (nrow(df) == 0L) stop("去重后语料为空")

  # SR（短引用）列：bibliometrix 以 SR 作文档主键。tableTag/biblioNetwork 等会先做
  # `M[!duplicated(M$SR), ]`，缺 SR 时 M$SR 为 NULL → 选中零行 → 关键词词频/共现网络
  # 整体落空。convert2df 路径(OpenAlex)会自动建 SR，但保真 records 路径必须自建，
  # 否则任何由 records 物化的语料(PDF/手动/Sciverse/from-search)都做不出关键词分析。
  if (!"SR" %in% colnames(df)) df$SR <- .make_sr(df)

  # bibliometrix 标记（convert2df 会打的 attr）
  attr(df, "class")    <- c("bibliometrixDB", "data.frame")
  attr(df, "dbsource") <- "bibliocn"
  attr(df, "format")   <- "api"

  df
}

# ---- 顶层入口: records list/df → 存储 → meta ----
#' @param records list-of-lists 或 data.frame（来自 plumber JSON 解码）
#' @return save_corpus() 返回的 meta list
parse_from_records_and_store <- function(records, corpus_id = new_corpus_id()) {
  tryCatch({
    df <- records_to_bib_df(records)
    save_corpus(df, corpus_id, dbsource = "bibliocn", status = "ready")
  }, error = function(e) {
    save_corpus(NULL, corpus_id, dbsource = "bibliocn", status = "failed",
                error = paste0("records 建库失败: ", conditionMessage(e)))
  })
}
