# services/r-analysis/R/analysis_pages.R
# DTOs for source, author and document analysis pages.

sources_dto <- function(M, top = 20L) {
  if (!is.data.frame(M) || nrow(M) == 0L) stop("DATA_QUALITY|sources_dto: 语料为空")
  if (!"SO" %in% names(M)) stop("DATA_QUALITY|sources_dto: 语料缺来源刊字段(SO)")
  top <- max(1L, as.integer(top))

  so <- utils::head(sort(table(M$SO), decreasing = TRUE), top)
  top_sources <- unname(Map(
    function(s, n) list(source = as.character(s), articles = as.integer(n)),
    names(so), as.integer(so)
  ))

  gmt <- hindex_gmt_map(M, "source")
  h_index <- tryCatch({
    H <- bibliometrix::Hindex(
      M, field = "source", elements = NULL,
      sep = ";", years = Inf
    )$H
    H <- utils::head(H[order(-H$h_index), , drop = FALSE], top)
    unname(Map(function(el, h) {
      el <- as.character(el)
      ex <- gmt[[el]]
      c(
        list(source = el, h = as.integer(h)),
        if (!is.null(ex)) ex[c("g", "m", "tc")] else NULL
      )
    }, H$Element, H$h_index))
  }, error = function(e) list())

  bradford <- tryCatch({
    b <- bibliometrix::bradford(M)$table
    total <- sum(as.numeric(b$Freq), na.rm = TRUE)
    b <- utils::head(b, top)
    cum <- cumsum(as.numeric(b$Freq))
    unname(Map(function(s, z, f, rk, cf) list(
      source = as.character(s),
      zone = as.character(z),
      freq = as.integer(f),
      rank = as.integer(rk),
      cumPct = if (total > 0) round(100 * cf / total, 1) else 0
    ), b$SO, b$Zone, b$Freq, seq_len(nrow(b)), cum))
  }, error = function(e) list())

  list(
    schemaVersion = 1L,
    topSources = top_sources,
    hIndex = h_index,
    bradford = bradford
  )
}

authors_dto <- function(M, top = 20L) {
  if (!is.data.frame(M) || nrow(M) == 0L) stop("DATA_QUALITY|authors_dto: 语料为空")
  if (!"AU" %in% names(M)) stop("DATA_QUALITY|authors_dto: 语料缺作者字段(AU)")
  top <- max(1L, as.integer(top))

  res <- bibliometrix::biblioAnalysis(M, sep = ";")
  mp <- utils::head(as.data.frame(res$Authors, stringsAsFactors = FALSE), top)
  top_authors <- unname(Map(
    function(a, n) list(author = as.character(a), articles = as.integer(n)),
    mp[[1]], mp[[2]]
  ))

  gmt <- hindex_gmt_map(M, "author")
  h_index <- tryCatch({
    H <- bibliometrix::Hindex(
      M, field = "author", elements = NULL,
      sep = ";", years = Inf
    )$H
    H <- utils::head(H[order(-H$h_index), , drop = FALSE], top)
    unname(Map(function(el, h) {
      el <- as.character(el)
      ex <- gmt[[el]]
      c(
        list(author = el, h = as.integer(h)),
        if (!is.null(ex)) ex[c("g", "m", "tc")] else NULL
      )
    }, H$Element, H$h_index))
  }, error = function(e) list())

  lotka <- tryCatch({
    M_db <- M
    if (!inherits(M_db, "bibliometrixDB")) class(M_db) <- c("bibliometrixDB", class(M_db))
    lk <- bibliometrix::lotka(M_db)
    ap <- lk$AuthorProd
    list(
      beta = round(as.numeric(lk$Beta), 4),
      distribution = unname(Map(
        function(a, n) list(articles = as.integer(a), authors = as.integer(n)),
        ap[[1]], ap[[2]]
      ))
    )
  }, error = function(e) list(distribution = list()))

  list(
    schemaVersion = 1L,
    topAuthors = top_authors,
    hIndex = h_index,
    lotka = lotka
  )
}

.de_keyword_freq <- function(M, top) {
  wf <- tryCatch(
    bibliometrix::tableTag(M, Tag = "DE", sep = ";"),
    error = function(e) integer(0)
  )
  if (length(wf) == 0L && "DE" %in% names(M)) {
    terms <- toupper(trimws(unlist(strsplit(
      paste(as.character(M$DE), collapse = ";"),
      ";",
      fixed = TRUE
    ))))
    terms <- terms[!is.na(terms) & nzchar(terms)]
    wf <- sort(table(terms), decreasing = TRUE)
  }
  if (length(wf) == 0L) return(list())
  wf <- utils::head(wf, top)
  unname(Map(
    function(t, f) list(term = as.character(t), freq = as.integer(f)),
    names(wf), as.integer(wf)
  ))
}

documents_dto <- function(M, top = 20L) {
  if (!is.data.frame(M) || nrow(M) == 0L) stop("DATA_QUALITY|documents_dto: 语料为空")
  top <- max(1L, as.integer(top))

  tc <- if ("TC" %in% names(M)) suppressWarnings(as.numeric(M$TC)) else rep(NA_real_, nrow(M))
  ord <- order(tc, decreasing = TRUE)
  k <- min(top, nrow(M))
  get <- function(col, i) if (col %in% names(M)) as.character(M[[col]][i]) else NA_character_
  top_cited <- unname(lapply(ord[seq_len(k)], function(i) {
    rec <- list(
      title = {
        v <- get("TI", i)
        if (is.na(v)) NULL else v
      },
      author = {
        v <- get("AU", i)
        if (is.na(v)) NULL else v
      },
      year = {
        y <- suppressWarnings(as.integer(get("PY", i)))
        if (is.na(y)) NULL else y
      },
      cited = {
        v <- tc[i]
        if (is.na(v)) 0L else as.integer(v)
      }
    )
    rec[!vapply(rec, is.null, logical(1))]
  }))

  list(
    schemaVersion = 1L,
    topCited = top_cited,
    keywords = .de_keyword_freq(M, top)
  )
}
