# R6 模块 UI 烟雾测
testthat::skip_if_not_installed("shiny")
testthat::skip_if_not_installed("bs4Dash")
library(shiny); library(bs4Dash)
source(file.path("..","..","R","ui_helpers.R"))
source(file.path("..","..","R","mod_ai_cite.R"))
source(file.path("..","..","R","mod_settings.R"))
if (!exists("LBL")) LBL <- list(no_data = "请上传")

test_that("aiCiteUI 含 style 与 verify 按钮", {
  ui <- aiCiteUI("c")
  expect_s3_class(ui, "shiny.tag.list")
  expect_true(grepl("style", as.character(ui), fixed = TRUE))
  expect_true(grepl("verify", as.character(ui), fixed = TRUE))
})

test_that("settingsUI 含 key_status 与 cost_table", {
  ui <- settingsUI("s")
  expect_s3_class(ui, "shiny.tag.list")
  expect_true(grepl("key_status", as.character(ui), fixed = TRUE))
  expect_true(grepl("cost_table", as.character(ui), fixed = TRUE))
})
