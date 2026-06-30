# R4-3: mod_ai_translate / mod_ai_summary UI 烟雾测
testthat::skip_if_not_installed("shiny")
testthat::skip_if_not_installed("bs4Dash")
library(shiny); library(bs4Dash)
source(file.path("..","..","R","ui_helpers.R"))
source(file.path("..","..","R","mod_ai_translate.R"))
source(file.path("..","..","R","mod_ai_summary.R"))
if (!exists("LBL")) LBL <- list(no_data = "请上传")

test_that("aiTranslateUI 含方向/字段/Top-N 控件", {
  ui <- aiTranslateUI("tr")
  expect_s3_class(ui, "shiny.tag.list")
  html <- as.character(ui)
  expect_true(grepl("dir", html, fixed = TRUE))
  expect_true(grepl("field", html, fixed = TRUE))
  expect_true(grepl("top_n", html, fixed = TRUE))
})

test_that("aiSummaryUI 含 top_n / 按钮", {
  ui <- aiSummaryUI("sm")
  expect_s3_class(ui, "shiny.tag.list")
  expect_true(grepl("top_n", as.character(ui), fixed = TRUE))
})
