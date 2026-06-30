# R2-3: fct_cite.R 单测 (GB/T 7714 / APA-7 / MLA-9)
source(file.path("..", "..", "R", "fct_cite.R"))

test_record <- function() list(
  authors = c("Aria, M.", "Cuccurullo, C."),
  year    = 2017L,
  title   = "bibliometrix: An R-tool for comprehensive science mapping analysis",
  journal = "Journal of Informetrics",
  volume  = "11", issue = "4", pages = "959-975",
  doi     = "10.1016/j.joi.2017.08.007"
)

test_that("GB/T 7714-2015 期刊格式正确", {
  s <- format_citation(test_record(), style = "gbt7714")
  expect_true(grepl("Aria M, Cuccurullo C", s, fixed = TRUE))
  expect_true(grepl("2017", s, fixed = TRUE))
  expect_true(grepl("11(4)", s, fixed = TRUE))
  expect_true(grepl("959-975", s, fixed = TRUE))
  expect_true(grepl("[J]", s, fixed = TRUE))
})

test_that("GB/T 7714 超过 3 个作者用 '等'", {
  r <- test_record()
  r$authors <- c("Aria, M.", "Cuccurullo, C.", "Wang, X.", "Liu, Y.", "Zhang, M.")
  s <- format_citation(r, style = "gbt7714")
  expect_true(grepl("等", s, fixed = TRUE))
})

test_that("APA-7 双作者用 &", {
  s <- format_citation(test_record(), style = "apa")
  expect_true(grepl("Aria, M., & Cuccurullo, C.", s, fixed = TRUE))
  expect_true(grepl("(2017)", s, fixed = TRUE))
  expect_true(grepl("Journal of Informetrics, 11(4), 959-975", s, fixed = TRUE))
  expect_true(grepl("https://doi.org/10.1016/j.joi.2017.08.007", s, fixed = TRUE))
})

test_that("MLA-9 双作者第二位 名前姓后", {
  s <- format_citation(test_record(), style = "mla")
  expect_true(grepl("Aria, M. and C Cuccurullo", s, fixed = TRUE))
  expect_true(grepl("\"bibliometrix", s, fixed = TRUE))
  expect_true(grepl("vol. 11, no. 4, 2017, pp. 959-975", s, fixed = TRUE))
})

test_that("export_bibliography 从 bibliometrix M 整库导出 GB/T 7714", {
  M <- data.frame(
    AU = c("ARIA M;CUCCURULLO C", "WANG X;LIU Y"),
    PY = c(2017L, 2020L),
    TI = c("bibliometrix: An R-tool", "Scientometric study"),
    SO = c("Journal of Informetrics", "Scientometrics"),
    VL = c("11", "125"),
    IS = c("4", "2"),
    PP = c("959-975", "100-120"),
    DI = c("10.1016/j.joi.2017.08.007", "10.1007/s11192-020-03434-4"),
    stringsAsFactors = FALSE
  )
  tf <- tempfile(fileext = ".txt")
  export_bibliography(M, style = "gbt7714", path = tf)
  txt <- readLines(tf, encoding = "UTF-8")
  expect_length(txt, 2L)
  expect_true(grepl("Aria M", txt[1], fixed = TRUE))
  expect_true(grepl("Wang X", txt[2], fixed = TRUE))
})

test_that("缺字段时仍能产出 (Anon. / NA 不崩)", {
  M <- data.frame(AU = NA, PY = NA, TI = "X", SO = "Y",
                  VL = NA, IS = NA, PP = NA, DI = NA,
                  stringsAsFactors = FALSE)
  s_gbt <- format_citation(fct_cite_record <- list(
    authors = character(0), year = NA_integer_, title = "X", journal = "Y",
    volume = "", issue = "", pages = "", doi = ""), style = "gbt7714")
  expect_true(nzchar(s_gbt))
})
