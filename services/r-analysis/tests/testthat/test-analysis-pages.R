test_that("sources_dto 结构正确 + JSON 可序列化", {
  data(scientometrics, package = "bibliometrixData")
  d <- sources_dto(scientometrics, top = 10)
  expect_equal(d$schemaVersion, 1L)
  expect_true(length(d$topSources) > 0 && length(d$topSources) <= 10)
  expect_true(all(c("source", "articles") %in% names(d$topSources[[1]])))
  expect_true(is.list(d$hIndex))
  expect_true(is.list(d$bradford))
  expect_true(jsonlite::validate(jsonlite::toJSON(d, auto_unbox = TRUE, null = "null")))
})

test_that("authors_dto 结构正确 + Lotka", {
  data(scientometrics, package = "bibliometrixData")
  d <- authors_dto(scientometrics, top = 10)
  expect_true(length(d$topAuthors) > 0)
  expect_true(all(c("author", "articles") %in% names(d$topAuthors[[1]])))
  expect_true(is.list(d$hIndex))
  expect_true(is.list(d$lotka))
  expect_true(jsonlite::validate(jsonlite::toJSON(d, auto_unbox = TRUE, null = "null")))
})

test_that("documents_dto 高被引降序 + 关键词", {
  data(scientometrics, package = "bibliometrixData")
  d <- documents_dto(scientometrics, top = 10)
  expect_true(length(d$topCited) > 0)
  cited <- vapply(d$topCited, function(x) as.integer(x$cited %||% 0L), integer(1))
  expect_true(all(diff(cited) <= 0))  # 被引降序
  expect_true(is.list(d$keywords))
  expect_true(jsonlite::validate(jsonlite::toJSON(d, auto_unbox = TRUE, null = "null")))
})

test_that("空语料报错", {
  expect_error(sources_dto(data.frame()), "empty")
  expect_error(authors_dto(data.frame()), "empty")
  expect_error(documents_dto(data.frame()), "empty")
})
