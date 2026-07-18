# services/r-analysis/R/cite.R — 引用格式化 (移植自 legacy fct_cite.R)
# GB/T 7714-2015 / APA-7 / MLA-9。确定性, 无 LLM。需 M 的 SO/VL/IS/PP 字段。

# 标量清洗: NULL/NA/NaN/length0 → "" (Codex slice6-P1: 防 nzchar(NA) 报错与 "NA" 垃圾)
.s <- function(x) {
  if (is.null(x) || length(x) == 0L) return("")
  v <- x[1]
  if (is.na(v)) "" else as.character(v)
}

# 解析作者名 → (surname, given tokens)。surname 取最长 token：对 WoS 'LASTNAME FI'(姓在前)、
# 自然 'First Last'(姓在后)、'Last, First'(逗号) 都稳——姓通常比名/缩写长。
.au_parts <- function(name) {
  n <- trimws(name)
  fam <- ""
  if (grepl(",", n, fixed = TRUE)) fam <- trimws(strsplit(n, ",", fixed = TRUE)[[1]][1])
  toks <- tolower(strsplit(gsub("[^[:alnum:][:space:]]", " ", n), "\\s+")[[1]])
  toks <- toks[nzchar(toks)]
  if (nzchar(fam)) fam <- tolower(gsub("[^[:alnum:]]", "", fam))
  else if (length(toks)) fam <- toks[which.max(nchar(toks))]
  given <- toks[toks != fam]
  list(surname = fam, given = given)
}

# given 是否兼容(同一人): 短的一方每个 token 都能在长的一方找到 相等 或 缩写前缀 匹配。
# 关键: 'john' vs 'jane' 不兼容(都是全词、不相等、非单字母缩写) → 不误并不同人(codex P1)。
.given_compatible <- function(g1, g2) {
  short <- if (length(g1) <= length(g2)) g1 else g2
  long  <- if (length(g1) <= length(g2)) g2 else g1
  if (!length(short)) return(TRUE)
  for (s in short) {
    ok <- any(vapply(long, function(l)
      s == l || (nchar(s) == 1L && startsWith(l, s)) || (nchar(l) == 1L && startsWith(s, l)),
      logical(1)))
    if (!ok) return(FALSE)
  }
  TRUE
}

# 去重同一作者的多写法(全名/缩写/姓在前)。既有缓存语料的 AU 含同人 2-4 变体(Sciverse 脏+入库未去重)，
# cite 时去重即可修复无需重建。保守: 仅当 同姓 且 given 兼容 才并(防误并同姓不同人, codex P1)；
# 同组优先保留含逗号 'Last, First' 形式(可消歧 family)，其次更长(更完整)。
.dedup_au <- function(tokens) {
  out <- character(0); gsur <- character(0); ggiv <- list(); gidx <- integer(0)
  for (tk in tokens) {
    p <- .au_parts(tk)
    toks <- tolower(strsplit(gsub("[^[:alnum:][:space:]]", " ", tk), "\\s+")[[1]])
    toks <- toks[nzchar(toks)]
    hit <- 0L
    # 匹配既有组: 组的姓是否就在本名 token 里(等长平局也能正确归并, 如 'Ester Manik' ↔ 'Manik, Ester')
    for (j in seq_along(gsur)) {
      if (nzchar(gsur[j]) && gsur[j] %in% toks &&
          .given_compatible(toks[toks != gsur[j]], ggiv[[j]])) { hit <- j; break }
    }
    if (hit == 0L) {
      out <- c(out, tk); gsur <- c(gsur, p$surname); ggiv <- c(ggiv, list(p$given)); gidx <- c(gidx, length(out))
    } else {
      cur <- out[gidx[hit]]; n_c <- grepl(",", tk, fixed = TRUE); c_c <- grepl(",", cur, fixed = TRUE)
      if ((n_c && !c_c) || (n_c == c_c && nchar(tk) > nchar(cur))) out[gidx[hit]] <- tk
      if (n_c && nzchar(p$surname)) { gsur[hit] <- p$surname; ggiv[[hit]] <- p$given }  # 逗号形式 family 更可靠
    }
  }
  out
}

.record_from_M_row <- function(row) {
  au_raw <- .s(row$AU)
  au <- if (nzchar(au_raw)) strsplit(au_raw, ";", fixed = TRUE)[[1]] else character(0)
  au <- .dedup_au(trimws(au))
  au <- vapply(au, function(a) {
    a <- trimws(a)
    if (grepl(",", a, fixed = TRUE)) {
      # 'Last, First Middle' 形式(Sciverse 常见): 逗号前=family, 逗号后取首字母。
      # 旧逻辑按空格切会把 "Utami, Elok Sri" 误成 family="Utami," → 双逗号 "Utami,, E. S."。
      fg <- strsplit(a, ",", fixed = TRUE)[[1]]
      family <- tools::toTitleCase(tolower(trimws(fg[1])))
      given <- if (length(fg) >= 2L) trimws(paste(fg[-1], collapse = " ")) else ""
      gp <- strsplit(given, "\\s+")[[1]]; gp <- gp[nzchar(gp)]
      if (length(gp)) {
        initials <- paste(paste0(toupper(substr(gp, 1, 1)), "."), collapse = " ")
        sprintf("%s, %s", family, initials)
      } else family
    } else {
      parts <- strsplit(a, " ", fixed = TRUE)[[1]]
      parts <- parts[nzchar(parts)]
      if (length(parts) >= 2) {
        family <- tools::toTitleCase(tolower(parts[1]))
        initials <- paste(paste0(toupper(substr(parts[-1], 1, 1)), "."), collapse = " ")
        sprintf("%s, %s", family, initials)
      } else a
    }
  }, character(1), USE.NAMES = FALSE)
  yr <- suppressWarnings(as.integer(.s(row$PY)))
  list(
    authors = au,
    year    = if (is.na(yr)) "" else as.character(yr),
    title   = .s(row$TI),
    journal = .s(row$SO),
    volume  = .s(row$VL),
    issue   = .s(row$IS),
    pages   = .s(row$PP),
    doi     = .s(row$DI)
  )
}

# Vol(Issue) 片段：缺则省略，避免输出空的 "()"。
.vi_seg <- function(vol, iss) {
  vol <- trimws(vol %||% ""); iss <- trimws(iss %||% "")
  if (nzchar(vol) && nzchar(iss)) sprintf("%s(%s)", vol, iss)
  else if (nzchar(vol)) vol
  else if (nzchar(iss)) sprintf("(%s)", iss)
  else ""
}

.fmt_gbt7714 <- function(r) {
  if (length(r$authors) == 0L) {
    jn <- trimws(r$journal %||% "")
    return(if (nzchar(jn)) sprintf("%s[J]. %s.", r$title, jn) else sprintf("%s[J].", r$title))
  }
  au <- vapply(r$authors, function(a) {
    parts <- strsplit(a, ",\\s*")[[1]]
    if (length(parts) == 2) paste0(parts[1], " ", gsub("\\.", "", parts[2])) else a
  }, character(1))
  au_str <- if (length(au) > 3L) paste(paste(au[1:3], collapse = ", "), "等", sep = ", ")
            else paste(au, collapse = ", ")
  yr <- as.character(r$year %||% "")
  jyv <- Filter(nzchar, c(trimws(r$journal %||% ""), yr, .vi_seg(r$volume, r$issue)))
  pg <- trimws(r$pages %||% "")
  src <- if (length(jyv)) paste0(paste(jyv, collapse = ", "),
                                 if (nzchar(pg)) sprintf(": %s", pg) else "", ".") else ""
  if (nzchar(src)) sprintf("%s. %s[J]. %s", au_str, r$title, src)
  else sprintf("%s. %s[J].", au_str, r$title)
}

# APA-7 题名 sentence case (F-19)：仅当题名(近)全大写（无小写字母，或有大小写字符中
# >=90% 为大写）时转换——先整体小写化，再大写首字母与冒号后第一个字母；
# 混合大小写题名原样保留。
.sentence_case <- function(title) {
  t <- trimws(title %||% "")
  if (!nzchar(t)) return(t)
  chars <- strsplit(gsub("[^[:alpha:]]", "", t), "", fixed = TRUE)[[1]]
  cased <- chars[tolower(chars) != toupper(chars)]
  if (!length(cased)) return(t)
  if (mean(cased == toupper(cased)) < 0.9) return(t)
  t <- tolower(t)
  substr(t, 1L, 1L) <- toupper(substr(t, 1L, 1L))
  m <- regexpr(":[[:space:]]*[[:alpha:]]", t, perl = TRUE)
  if (m > 0L) {
    i <- m + attr(m, "match.length") - 1L
    substr(t, i, i) <- toupper(substr(t, i, i))
  }
  t
}

.fmt_apa <- function(r) {
  au_str <- if (length(r$authors) == 0L) "Anon."
            else if (length(r$authors) == 1L) r$authors[1]
            else if (length(r$authors) == 2L) paste(r$authors, collapse = ", & ")
            else paste(paste(r$authors[-length(r$authors)], collapse = ", "),
                       "&", r$authors[length(r$authors)])
  yr <- if (nzchar(as.character(r$year %||% ""))) as.character(r$year) else "n.d."
  head <- sprintf("%s (%s). %s.", au_str, yr, .sentence_case(r$title))
  # 来源段(Journal, Vol(Issue), Pages.)各段缺则省略, 避免 ", (), ."
  src_parts <- Filter(nzchar, c(trimws(r$journal %||% ""), .vi_seg(r$volume, r$issue), trimws(r$pages %||% "")))
  base <- if (length(src_parts)) sprintf("%s %s.", head, paste(src_parts, collapse = ", ")) else head
  if (nzchar(r$doi %||% "")) paste0(base, " https://doi.org/", r$doi) else base
}

.fmt_mla <- function(r) {
  au_str <- if (length(r$authors) == 0L) "Anon."
            else if (length(r$authors) == 1L) r$authors[1]
            else if (length(r$authors) == 2L) {
              sec_parts <- strsplit(r$authors[2], ",\\s*")[[1]]
              if (length(sec_parts) == 2L)
                paste(r$authors[1], "and", paste(gsub("\\.", "", sec_parts[2]), sec_parts[1]))
              else paste(r$authors[1], "and", r$authors[2])
            } else paste(r$authors[1], "et al.")
  au_str <- sub("\\.\\s*$", "", au_str)  # 去 au_str 末尾句点, 防 MLA "Smith, J.. " 双句点
  segs <- Filter(nzchar, c(
    trimws(r$journal %||% ""),
    if (nzchar(trimws(r$volume %||% ""))) sprintf("vol. %s", trimws(r$volume)) else "",
    if (nzchar(trimws(r$issue %||% ""))) sprintf("no. %s", trimws(r$issue)) else "",
    as.character(r$year %||% ""),
    if (nzchar(trimws(r$pages %||% ""))) sprintf("pp. %s", trimws(r$pages)) else ""
  ))
  if (length(segs)) sprintf("%s. \"%s.\" %s.", au_str, r$title, paste(segs, collapse = ", "))
  else sprintf("%s. \"%s.\"", au_str, r$title)
}

format_citation <- function(record, style = "apa") {
  switch(style,
    gbt7714 = .fmt_gbt7714(record),
    apa     = .fmt_apa(record),
    mla     = .fmt_mla(record),
    stop(sprintf("不支持的引用格式: %s", style)))
}

#' 整库引用导出 → 字符向量
corpus_citations <- function(M, style = "apa", limit = 200L) {
  if (!is.data.frame(M) || nrow(M) == 0L) return(character(0))
  if (!style %in% c("gbt7714", "apa", "mla")) stop("不支持的引用格式")
  n <- min(nrow(M), max(1L, as.integer(limit)))
  vapply(seq_len(n), function(i)
    tryCatch(format_citation(.record_from_M_row(M[i, , drop = FALSE]), style),
             error = function(e) ""),  # 单条失败不拖垮整批 (Codex slice6-P1)
    character(1))
}
