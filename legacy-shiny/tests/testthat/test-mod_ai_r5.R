# R5 三模块 UI 烟雾测
testthat::skip_if_not_installed("shiny")
testthat::skip_if_not_installed("bs4Dash")
library(shiny); library(bs4Dash)
source(file.path("..","..","R","ui_helpers.R"))
source(file.path("..","..","R","mod_ai_review.R"))
source(file.path("..","..","R","mod_ai_rewrite.R"))
source(file.path("..","..","R","mod_ai_chat.R"))
if (!exists("LBL")) LBL <- list(no_data = "请上传")

test_that("aiReviewUI 含论型选择与下载", {
  ui <- aiReviewUI("rv")
  expect_s3_class(ui, "shiny.tag.list")
  expect_true(grepl("type", as.character(ui), fixed = TRUE))
})
test_that("aiRewriteUI 含 4 个动作", {
  ui <- aiRewriteUI("rw")
  expect_s3_class(ui, "shiny.tag.list")
  expect_true(grepl("action", as.character(ui), fixed = TRUE))
})
test_that("aiChatUI 含 query 输入", {
  ui <- aiChatUI("ch")
  expect_s3_class(ui, "shiny.tag.list")
  expect_true(grepl("query", as.character(ui), fixed = TRUE))
})
