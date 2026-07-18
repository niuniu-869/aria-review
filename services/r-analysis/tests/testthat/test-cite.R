test_that("corpus_citations 三种格式可生成", {
  data(scientometrics, package = "bibliometrixData")
  for (st in c("gbt7714", "apa", "mla")) {
    cs <- corpus_citations(scientometrics, st, limit = 3)
    expect_length(cs, 3)
    expect_true(all(nzchar(cs)))
  }
})

test_that("format_citation APA 含年份与 DOI", {
  rec <- list(authors = c("Aria, M."), year = 2017L, title = "Bibliometrix",
              journal = "J Informetrics", volume = "11", issue = "4",
              pages = "959", doi = "10.1016/j.joi.2017.08.007")
  s <- format_citation(rec, "apa")
  expect_match(s, "2017")
  expect_match(s, "doi.org")
})

test_that("不支持格式报错", {
  M <- data.frame(AU = "SMALL H", PY = 2020, TI = "t", SO = "s", stringsAsFactors = FALSE)
  expect_error(corpus_citations(M, "bad"), "不支持")
})

test_that("空语料返回空向量", {
  expect_equal(corpus_citations(data.frame()), character(0))
})

test_that(".record_from_M_row parses 'Last, First' authors without double comma", {
  row <- data.frame(AU="UTAMI, ELOK SRI;GUMANTI, TATANG ARY", PY=2015L, TI="T",
                    SO="", VL="", IS="", PP="", DI="", stringsAsFactors=FALSE)
  r <- .record_from_M_row(row)
  expect_equal(r$authors[1], "Utami, E. S.")
  expect_false(any(grepl(",,", r$authors, fixed = TRUE)))
})

test_that(".fmt_apa omits empty source fields (no ', (), .')", {
  r <- list(authors=c("Smith, J."), year="2015", title="T",
            journal="", volume="", issue="", pages="", doi="")
  out <- .fmt_apa(r)
  expect_false(grepl(", (), .", out, fixed = TRUE))
  expect_false(grepl("()", out, fixed = TRUE))
})

test_that(".record_from_M_row dedups duplicate author variants in cached AU", {
  # 既有语料 AU 含同人多写法(全名/缩写/姓在前) → cite 时去重为唯一作者
  au <- paste(c("ELOK SRI UTAMI","E. S. UTAMI","UTAMI, ELOK SRI",
                "TATANG ARY GUMANTI","GUMANTI, TATANG ARY"), collapse=";")
  row <- data.frame(AU=au, PY=2015L, TI="T", SO="", VL="", IS="", PP="", DI="",
                    stringsAsFactors=FALSE)
  r <- .record_from_M_row(row)
  expect_equal(length(r$authors), 2L)              # 5 变体 → 2 位真作者
  expect_true(all(grepl("^(Utami|Gumanti),", r$authors)))  # family 取对
})

test_that(".dedup_au does NOT merge distinct authors (codex P1)", {
  mk <- function(au) .record_from_M_row(data.frame(AU=paste(au,collapse=";"),PY=2015L,TI="T",
    SO="",VL="",IS="",PP="",DI="",stringsAsFactors=FALSE))$authors
  expect_equal(length(mk(c("SMITH J","SANDERS J"))), 2L)   # 不同姓
  expect_equal(length(mk(c("JOHN SMITH","JANE SMITH"))), 2L) # 同姓不同名
})

test_that(".fmt_apa converts ALL-CAPS title to sentence case (F-19)", {
  r <- list(authors=c("Li, S."), year="2023",
            title="TONE OF LANGUAGE, FINANCIAL DISCLOSURE, AND MARKET REACTION: EVIDENCE FROM CHINA",
            journal="J Finance", volume="10", issue="2", pages="100-120", doi="")
  out <- .fmt_apa(r)
  expect_match(out, "Tone of language, financial disclosure, and market reaction: Evidence from china.",
               fixed = TRUE)
  expect_false(grepl("TONE OF LANGUAGE", out, fixed = TRUE))
})

test_that(".fmt_apa keeps mixed-case title unchanged (F-19)", {
  r <- list(authors=c("Li, S."), year="2023",
            title="Tone of Language, Financial Disclosure, and Market Reaction",
            journal="", volume="", issue="", pages="", doi="")
  out <- .fmt_apa(r)
  expect_match(out, "Tone of Language, Financial Disclosure, and Market Reaction.", fixed = TRUE)
})
