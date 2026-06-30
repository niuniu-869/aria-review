# R4-2: mod_ai_screen UI 烟雾测
testthat::skip_if_not_installed("shiny")
testthat::skip_if_not_installed("bs4Dash")
library(shiny); library(bs4Dash)
source(file.path("..","..","R","ui_helpers.R"))
source(file.path("..","..","R","mod_ai_screen.R"))
if (!exists("LBL")) LBL <- list(no_data = "请上传")

test_that("aiScreenUI 返回 shiny.tag.list 且含主题输入", {
  ui <- aiScreenUI("t")
  expect_s3_class(ui, "shiny.tag.list")
  html <- as.character(ui)
  expect_true(grepl("topic", html, fixed = TRUE))
  expect_true(grepl("threshold", html, fixed = TRUE))
})
