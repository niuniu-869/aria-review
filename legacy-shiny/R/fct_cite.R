# R/fct_cite.R — 引用格式化 (GB/T 7714-2015, APA-7, MLA-9)

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a

#' 把 bibliometrix M data.frame 的一行规范化为 record list
#'
#' bibliometrix 作者形如 "ARIA M;CUCCURULLO C", 转为 "Aria, M."
.record_from_M_row <- function(row) {
  au_raw <- row$AU
  au <- if (length(au_raw) && !is.na(au_raw) && nzchar(au_raw))
    strsplit(as.character(au_raw), ";", fixed = TRUE)[[1]]
  else
    character(0)
  au <- vapply(au, function(a) {
    a <- trimws(a)
    parts <- strsplit(a, " ", fixed = TRUE)[[1]]
    parts <- parts[nzchar(parts)]
    if (length(parts) >= 2) {
      family <- tools::toTitleCase(tolower(parts[1]))
      initials <- paste(paste0(toupper(substr(parts[-1], 1, 1)), "."), collapse = " ")
      sprintf("%s, %s", family, initials)
    } else a
  }, character(1))
  list(
    authors = au,
    year    = suppressWarnings(as.integer(row$PY %||% NA)),
    title   = as.character(row$TI %||% ""),
    journal = as.character(row$SO %||% ""),
    volume  = as.character(row$VL %||% ""),
    issue   = as.character(row$IS %||% ""),
    pages   = as.character(row$PP %||% ""),
    doi     = as.character(row$DI %||% "")
  )
}

#' 单条引用格式化
format_citation <- function(record, style = c("gbt7714", "apa", "mla")) {
  style <- match.arg(style)
  switch(style,
    gbt7714 = .fmt_gbt7714(record),
    apa     = .fmt_apa(record),
    mla     = .fmt_mla(record)
  )
}

# ── GB/T 7714-2015 顺序编码制 (期刊文献 [J]) ───────────────────────────────
# 作者: 姓 + 名首字母 (无逗号), 多作者用逗号分; 多于 3 个用"等"
.fmt_gbt7714 <- function(r) {
  if (length(r$authors) == 0L) return(sprintf("%s[J]. %s.", r$title, r$journal))
  au <- vapply(r$authors, function(a) {
    parts <- strsplit(a, ",\\s*")[[1]]
    if (length(parts) == 2) paste0(parts[1], " ", gsub("\\.", "", parts[2]))
    else a
  }, character(1))
  au_str <- if (length(au) > 3L)
    paste(paste(au[1:3], collapse = ", "), "等", sep = ", ")
  else paste(au, collapse = ", ")
  sprintf("%s. %s[J]. %s, %d, %s(%s): %s.",
          au_str, r$title, r$journal,
          r$year %||% NA_integer_,
          r$volume %||% "", r$issue %||% "", r$pages %||% "")
}

# ── APA-7 ────────────────────────────────────────────────────────────────
.fmt_apa <- function(r) {
  au_str <- if (length(r$authors) == 0L) "Anon."
            else if (length(r$authors) == 1L) r$authors[1]
            else if (length(r$authors) == 2L) paste(r$authors, collapse = ", & ")
            else paste(paste(r$authors[-length(r$authors)], collapse = ", "),
                       "&", r$authors[length(r$authors)])
  base <- sprintf("%s (%d). %s. %s, %s(%s), %s.",
                  au_str, r$year %||% NA_integer_,
                  r$title, r$journal,
                  r$volume %||% "", r$issue %||% "", r$pages %||% "")
  if (nzchar(r$doi %||% "")) paste0(base, " https://doi.org/", r$doi) else base
}

# ── MLA-9 ─────────────────────────────────────────────────────────────────
.fmt_mla <- function(r) {
  au_str <- if (length(r$authors) == 0L) "Anon."
            else if (length(r$authors) == 1L) r$authors[1]
            else if (length(r$authors) == 2L) {
              # "Aria, M. and C. Cuccurullo" (第二作者改为名在前)
              sec_parts <- strsplit(r$authors[2], ",\\s*")[[1]]
              if (length(sec_parts) == 2L)
                paste(r$authors[1], "and",
                      paste(gsub("\\.", "", sec_parts[2]), sec_parts[1]))
              else paste(r$authors[1], "and", r$authors[2])
            }
            else paste(r$authors[1], "et al.")
  sprintf("%s. \"%s.\" %s, vol. %s, no. %s, %d, pp. %s.",
          au_str, r$title, r$journal,
          r$volume %||% "", r$issue %||% "",
          r$year %||% NA_integer_, r$pages %||% "")
}

#' 整库导出
export_bibliography <- function(M, style = c("gbt7714", "apa", "mla"), path) {
  style <- match.arg(style)
  out <- vapply(seq_len(nrow(M)), function(i) {
    format_citation(.record_from_M_row(M[i, , drop = FALSE]), style = style)
  }, character(1))
  writeLines(out, path, useBytes = TRUE)
  invisible(path)
}
