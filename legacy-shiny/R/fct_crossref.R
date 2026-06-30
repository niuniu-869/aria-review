# R/fct_crossref.R — Crossref DOI 校验 (公开 API, 无需 key)

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a

#' 单 DOI 查询
#'
#' @return list(doi, title, authors, year, journal)
crossref_lookup <- function(doi,
                            email = "aria-review@users.noreply.github.com",
                            timeout_s = 30L) {
  url <- sprintf("https://api.crossref.org/works/%s?mailto=%s",
                 utils::URLencode(doi, reserved = TRUE),
                 utils::URLencode(email, reserved = TRUE))
  req <- httr2::request(url)
  req <- httr2::req_timeout(req, timeout_s)
  req <- httr2::req_throttle(req, rate = 30 / 60)
  req <- httr2::req_retry(req,
                          max_tries = 3,
                          backoff = function(i) 2 ^ i,
                          is_transient = function(resp) {
                            httr2::resp_status(resp) %in% c(429, 500, 502, 503, 504)
                          })
  req <- httr2::req_error(req, body = function(resp) {
    sprintf("Crossref API 错误 %d", httr2::resp_status(resp))
  })

  resp <- httr2::req_perform(req)
  body_raw <- if (is.raw(resp$body)) resp$body else charToRaw(as.character(resp$body))
  m <- jsonlite::fromJSON(rawToChar(body_raw), simplifyVector = FALSE)$message

  year <- NA_integer_
  for (k in c("published-print", "published-online", "issued")) {
    dp <- m[[k]]$`date-parts`
    if (length(dp) && length(dp[[1]])) {
      year <- as.integer(dp[[1]][[1]])
      break
    }
  }
  authors <- vapply(m$author %||% list(), function(a) {
    trimws(paste(a$given %||% "", a$family %||% ""))
  }, character(1))

  list(
    doi     = m$DOI,
    title   = (m$title %||% list(""))[[1]],
    authors = authors,
    year    = year,
    journal = (m$`container-title` %||% list(""))[[1]]
  )
}

#' 批量校验
#'
#' @return data.frame(doi, valid)
verify_citations <- function(dois) {
  res <- lapply(dois, function(d) {
    tryCatch(list(doi = d, valid = TRUE,  meta = crossref_lookup(d)),
             error = function(e) list(doi = d, valid = FALSE, meta = NULL))
  })
  data.frame(
    doi   = vapply(res, `[[`, character(1), "doi"),
    valid = vapply(res, `[[`, logical(1),   "valid"),
    stringsAsFactors = FALSE
  )
}
