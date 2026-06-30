# R3-3: mod_pdf_fetch UI 烟雾测
testthat::skip_if_not_installed("shiny")
testthat::skip_if_not_installed("bs4Dash")
library(shiny)
library(bs4Dash)
source(file.path("..", "..", "R", "ui_helpers.R"))
source(file.path("..", "..", "R", "mod_pdf_fetch.R"))

# global.R 的 LBL 在 testthat 单跑时不存在, mock 一个
if (!exists("LBL")) LBL <- list(no_data = "请上传")

test_that("pdfFetchUI 返回 shiny.tag.list", {
  ui <- pdfFetchUI("test")
  expect_s3_class(ui, "shiny.tag.list")
})
