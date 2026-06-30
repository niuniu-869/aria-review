test_that("conceptual_dto 返回 nodes/edges", {
  data(scientometrics, package = "bibliometrixData")
  d <- conceptual_dto(scientometrics, n = 20)
  expect_equal(d$schemaVersion, 1L)
  expect_true(is.list(d$graph$nodes))
  expect_true(is.list(d$graph$edges))
  expect_true(length(d$graph$nodes) > 0)
  expect_true(all(c("id", "label", "value") %in% names(d$graph$nodes[[1]])))
  expect_true(jsonlite::validate(jsonlite::toJSON(d, auto_unbox = TRUE, null = "null")))
})

test_that("intellectual_dto 返回共被引网络", {
  data(scientometrics, package = "bibliometrixData")
  d <- intellectual_dto(scientometrics, n = 20)
  expect_true(is.list(d$graph$nodes))
  expect_true(jsonlite::validate(jsonlite::toJSON(d, auto_unbox = TRUE, null = "null")))
})

test_that("social_dto 作者+国家双网络", {
  data(scientometrics, package = "bibliometrixData")
  d <- social_dto(scientometrics, n = 20)
  expect_true(is.list(d$authorCollab$nodes))
  expect_true(is.list(d$countryCollab$nodes))
  expect_true(jsonlite::validate(jsonlite::toJSON(d, auto_unbox = TRUE, null = "null")))
})

test_that(".net_dto 空矩阵安全", {
  out <- .net_dto(matrix(numeric(0), 0, 0))
  expect_equal(out, list(nodes = list(), edges = list()))
})

test_that("网络 dto 空语料报错", {
  expect_error(conceptual_dto(data.frame()), "空")
  expect_error(social_dto(data.frame()), "空")
})

test_that(".net_limit 钳制到 [1,100], NA/非数→100 (A5 §4.4)", {
  expect_equal(.net_limit(50), 50L)     # 正常区间原样
  expect_equal(.net_limit(100), 100L)   # 上界
  expect_equal(.net_limit(1), 1L)       # 下界
  expect_equal(.net_limit(101), 100L)   # 超上界 → 100
  expect_equal(.net_limit(0), 1L)       # 低于下界 → 钳到 1 (非 100)
  expect_equal(.net_limit(-5), 1L)      # 负数 → 1
  expect_equal(.net_limit(NA), 100L)    # NA → 默认 100
  expect_equal(.net_limit("abc"), 100L) # 非数 → 默认 100
})
