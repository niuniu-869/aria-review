# test-robustness.R — 坏输入健壮性测试（修复 2、1b 的回归保障）

# valid_corpus() 定义在 ui_helpers.R，需单独 source（helper-fixtures.R 只 source fct_analysis.R）
source(file.path("..", "..", "R", "ui_helpers.R"))

# ── valid_corpus 校验函数 ──────────────────────────────────────────────────────

test_that("valid_corpus 对有效语料返回 TRUE", {
  M <- test_corpus()
  expect_true(valid_corpus(M))
})

test_that("valid_corpus 对空 data.frame 返回 FALSE", {
  expect_false(valid_corpus(data.frame()))
})

test_that("valid_corpus 对 nrow=0 的 data.frame 返回 FALSE", {
  empty_M <- data.frame(AU = character(0), TI = character(0),
                        PY = integer(0), SO = character(0),
                        stringsAsFactors = FALSE)
  expect_false(valid_corpus(empty_M))
})

test_that("valid_corpus 对缺少核心列的 data.frame 返回 FALSE", {
  # 有行，但缺 TI
  bad_M <- data.frame(AU = "Author A", PY = 2020, TC = 5,
                      stringsAsFactors = FALSE)
  expect_false(valid_corpus(bad_M))
})

test_that("valid_corpus 对非 data.frame 返回 FALSE", {
  expect_false(valid_corpus(NULL))
  expect_false(valid_corpus(list(AU = "x", TI = "y", PY = 2020)))
})

# ── analyze_documents：字段缺失时应 stop ──────────────────────────────────────

test_that("analyze_documents 对空 data.frame 抛出字段缺失错误", {
  expect_error(analyze_documents(data.frame()), "缺少必需字段")
})

test_that("analyze_documents 对缺少 TC 列的语料抛出字段缺失错误", {
  M_no_tc <- test_corpus()
  M_no_tc$TC <- NULL
  expect_error(analyze_documents(M_no_tc), "缺少必需字段")
})

test_that("analyze_documents 对缺少 TI 列的语料抛出字段缺失错误", {
  M_no_ti <- test_corpus()
  M_no_ti$TI <- NULL
  expect_error(analyze_documents(M_no_ti), "缺少必需字段")
})

# ── analyze_sources：字段缺失时应 stop ────────────────────────────────────────

test_that("analyze_sources 对空 data.frame 抛出字段缺失错误", {
  expect_error(analyze_sources(data.frame()), "缺少必需字段")
})

test_that("analyze_sources 对缺少 SO 列的语料抛出字段缺失错误", {
  M_no_so <- test_corpus()
  M_no_so$SO <- NULL
  expect_error(analyze_sources(M_no_so), "缺少必需字段")
})

# ── analyze_authors：字段缺失时应 stop ────────────────────────────────────────

test_that("analyze_authors 对空 data.frame 抛出字段缺失错误", {
  expect_error(analyze_authors(data.frame()), "缺少必需字段")
})

test_that("analyze_authors 对缺少 AU 列的语料抛出字段缺失错误", {
  M_no_au <- test_corpus()
  M_no_au$AU <- NULL
  expect_error(analyze_authors(M_no_au), "缺少必需字段")
})

test_that("analyze_authors 对有 AU 无 TI 的语料抛出字段缺失错误", {
  M_partial <- data.frame(AU = c("Smith, J", "Lee, K"),
                          PY = c(2020L, 2021L),
                          stringsAsFactors = FALSE)
  expect_error(analyze_authors(M_partial), "缺少必需字段")
})

# ── analyze_sources：缺 TC 列时应 stop ────────────────────────────────────────

test_that("analyze_sources 对缺少 TC 列的语料抛出字段缺失错误", {
  M_no_tc <- test_corpus()
  M_no_tc$TC <- NULL
  expect_error(analyze_sources(M_no_tc), "缺少必需字段")
})

# ── analyze_authors：缺 TC 列时应 stop ────────────────────────────────────────

test_that("analyze_authors 对缺少 TC 列的语料抛出字段缺失错误", {
  M_no_tc <- test_corpus()
  M_no_tc$TC <- NULL
  expect_error(analyze_authors(M_no_tc), "缺少必需字段")
})
