# R3-1: fct_pdf.R 单测
source(file.path("..", "..", "R", "fct_pdf.R"))

test_that("pdf_extract_text 文件不存在时 warn 并返回空串", {
  expect_warning(out <- pdf_extract_text("/no/such/file.pdf"), "PDF")
  expect_equal(out, "")
})

test_that("pdf_extract_text 抽取真实 PDF (如果项目目录已有)", {
  pdfs <- list.files("/srv/shared/pdfs", "\\.pdf$", full.names = TRUE)
  testthat::skip_if(length(pdfs) == 0L, "需要 /srv/shared/pdfs 下有真实 PDF")
  txt <- pdf_extract_text(pdfs[1], max_pages = 2L)
  expect_true(nchar(txt) > 100)
})
