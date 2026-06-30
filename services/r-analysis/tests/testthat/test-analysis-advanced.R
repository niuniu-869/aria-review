# A4 高级图 + 信封 helper 单测。
# analysis_envelope 四个失败 reason 分支 + 成功分支; 三个 DTO 成功形状 + 缺字段降级。

# ---------- analysis_envelope helper ----------

test_that("analysis_envelope 成功分支 → available TRUE + data", {
  e <- analysis_envelope(function() list(a = 1L, b = 2L))
  expect_true(e$available)
  expect_equal(e$data$a, 1L)
})

test_that("analysis_envelope missing_field 分支 (字段缺)", {
  df <- data.frame(AU = "X", stringsAsFactors = FALSE)  # 无 CR
  e <- analysis_envelope(function() list(x = 1L),
                         required_fields = "CR", df = df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "CR")
  expect_true(nzchar(e$message))
})

test_that("analysis_envelope missing_field: 列存在但 100% 全空 (PDF 语料 CR/DE 空列)", {
  df <- data.frame(CR = c(NA, "", "  "), AU = c("A", "B", "C"),
                   stringsAsFactors = FALSE)  # CR 列在但全空
  e <- analysis_envelope(function() list(x = 1L),
                         required_fields = "CR", df = df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "CR")
})

test_that("analysis_envelope analysis_error 分支 (计算抛错)", {
  e <- analysis_envelope(function() stop("boom"))
  expect_false(e$available)
  expect_equal(e$reason, "analysis_error")
  expect_match(e$detail, "boom")
})

test_that("analysis_envelope not_enough_data 分支 (行数不足)", {
  # cells 长度 1 < min_rows 3 → not_enough_data
  e <- analysis_envelope(function() list(cells = list(list(x = 1L))),
                         min_rows = 3L)
  expect_false(e$available)
  expect_equal(e$reason, "not_enough_data")
})

test_that("analysis_envelope computed_empty 分支 (结果空)", {
  e1 <- analysis_envelope(function() list())
  expect_false(e1$available)
  expect_equal(e1$reason, "computed_empty")
  # cells 为空 list 也算空
  e2 <- analysis_envelope(function() list(authors = list(), years = list(), cells = list()))
  expect_false(e2$available)
  expect_equal(e2$reason, "computed_empty")
})

# ---------- 三个高级图 DTO (成功形状) ----------

test_that("author_production_dto 成功 → authors/years/cells", {
  data(scientometrics, package = "bibliometrixData")
  e <- author_production_dto(scientometrics, k = 5L)
  expect_true(e$available)
  d <- e$data
  expect_true(length(d$authors) > 0)
  expect_true(length(d$years) > 0)
  expect_true(length(d$cells) > 0)
  c1 <- d$cells[[1]]
  expect_true(all(c("author", "year", "articles") %in% names(c1)))
  expect_true(jsonlite::validate(jsonlite::toJSON(e, auto_unbox = TRUE, null = "null")))
})

test_that("author_production_dto 缺 PY → missing_field", {
  df <- data.frame(AU = "ARIA M", TI = "x", stringsAsFactors = FALSE)
  e <- author_production_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "PY")
})

test_that("keyword_trend_dto 成功 → years/terms/cells", {
  data(scientometrics, package = "bibliometrixData")
  e <- keyword_trend_dto(scientometrics, top_terms = 10L)
  expect_true(e$available)
  d <- e$data
  expect_true(length(d$years) > 0)
  expect_true(length(d$terms) > 0)
  expect_true(length(d$cells) > 0)
  c1 <- d$cells[[1]]
  expect_true(all(c("year", "term", "freq") %in% names(c1)))
  expect_true(jsonlite::validate(jsonlite::toJSON(e, auto_unbox = TRUE, null = "null")))
})

test_that("keyword_trend_dto 缺 DE → missing_field", {
  df <- data.frame(PY = 2020L, TI = "x", stringsAsFactors = FALSE)  # 无 DE
  e <- keyword_trend_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "DE")
})

test_that("cited_refs_dto 成功 → {ref, count}[]", {
  data(scientometrics, package = "bibliometrixData")
  e <- cited_refs_dto(scientometrics, top = 10L)
  expect_true(e$available)
  expect_true(length(e$data) > 0 && length(e$data) <= 10)
  r1 <- e$data[[1]]
  expect_true(all(c("ref", "count") %in% names(r1)))
  expect_true(is.character(r1$ref))
  expect_true(jsonlite::validate(jsonlite::toJSON(e, auto_unbox = TRUE, null = "null")))
})

test_that("cited_refs_dto 缺 CR → missing_field", {
  df <- data.frame(PY = 2020L, TI = "x", stringsAsFactors = FALSE)  # 无 CR
  e <- cited_refs_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "CR")
})

# ---------- g/m/tc 增量 ----------

test_that("hindex_gmt_map source 返回 g/m/tc, m 非有限→NULL", {
  data(scientometrics, package = "bibliometrixData")
  gmt <- hindex_gmt_map(scientometrics, "source")
  expect_true(length(gmt) > 0)
  one <- gmt[[1]]
  expect_true(all(c("g", "tc") %in% names(one)))
  expect_true(is.integer(one$g))
  # m 要么 NULL 要么有限数值
  if (!is.null(one$m)) expect_true(is.finite(one$m))
})

test_that("sources_dto hIndex 含 g/m/tc; bradford 含 rank/cumPct", {
  data(scientometrics, package = "bibliometrixData")
  d <- sources_dto(scientometrics, top = 10)
  expect_true(length(d$hIndex) > 0)
  h1 <- d$hIndex[[1]]
  expect_true(all(c("source", "h", "g", "tc") %in% names(h1)))  # m 可能因 NULL 被丢
  expect_true(length(d$bradford) > 0)
  b1 <- d$bradford[[1]]
  expect_true(all(c("source", "zone", "freq", "rank", "cumPct") %in% names(b1)))
  expect_equal(b1$rank, 1L)
  expect_true(jsonlite::validate(jsonlite::toJSON(d, auto_unbox = TRUE, null = "null")))
})

test_that("authors_dto hIndex 含 g/tc", {
  data(scientometrics, package = "bibliometrixData")
  d <- authors_dto(scientometrics, top = 10)
  expect_true(length(d$hIndex) > 0)
  h1 <- d$hIndex[[1]]
  expect_true(all(c("author", "h", "g", "tc") %in% names(h1)))
})

# ---------- overview hIndex / annualGrowthRate ----------

test_that("overview_dto stats 含 hIndex 与 annualGrowthRate (有 TC + 多年)", {
  data(scientometrics, package = "bibliometrixData")
  d <- overview_dto(scientometrics)
  expect_true(!is.null(d$stats$hIndex))
  expect_true(is.numeric(d$stats$hIndex) || is.integer(d$stats$hIndex))
  # annualGrowthRate 可能 NULL (边界), 但 scientometrics 多年且产出>0 → 应有值
  expect_true(!is.null(d$stats$annualGrowthRate))
})
