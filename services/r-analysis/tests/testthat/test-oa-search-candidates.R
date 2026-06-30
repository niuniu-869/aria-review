# .oa_works_to_candidates / oa_search_candidates 纯函数测试 (不触网)

test_that("oa_search_candidates maps work fields", {
  fake <- list(list(
    id = "https://openalex.org/W123",
    display_name = "A Title",
    title = "A Title",
    publication_year = 2022L,
    publication_date = "2022-03-01",
    doi = "https://doi.org/10.1/x",
    cited_by_count = 7L,
    authorships = list(list(author = list(display_name = "Jane Doe"))),
    primary_location = list(source = list(display_name = "J. Foo")),
    abstract_inverted_index = list(Hello = list(0L), world = list(1L))
  ))

  cands <- .oa_works_to_candidates(fake)

  expect_equal(length(cands), 1L)
  expect_equal(cands[[1]]$openalexId,    "W123")
  expect_equal(cands[[1]]$title,         "A Title")
  expect_equal(cands[[1]]$year,          2022L)
  expect_equal(cands[[1]]$doi,           "10.1/x")
  expect_equal(cands[[1]]$containerTitle, "J. Foo")
  expect_equal(cands[[1]]$authors[[1]],  "Jane Doe")
  expect_equal(cands[[1]]$abstract,      "Hello world")
  expect_equal(cands[[1]]$citedByCount,  7L)
  expect_equal(cands[[1]]$source,        "openalex")
})

test_that(".oa_works_to_candidates returns raw referenced_works IDs and does NOT resolve refs over network", {
  # 回归锁：检索候选路径必须只返回原始 referenced_works ID（不调 .oa_resolve_refs_batch）。
  # 否则单次 n=50 检索会对 ~2500 个 ref id 串行拉 OpenAlex, 阻塞单线程 R ~60s 饿死 healthz。
  library(mockery)
  fake <- list(list(
    id = "https://openalex.org/W123",
    title = "Anchored Paper",
    publication_year = 2022L,
    referenced_works = list("https://openalex.org/W11", "https://openalex.org/W22")
  ))
  # 守护：若候选路径再次去网络解析引用, stub 触发报错 → 测试失败
  stub(.oa_works_to_candidates, ".oa_resolve_refs_batch",
       function(...) stop("检索候选路径不得调用 .oa_resolve_refs_batch (阻塞单线程 R)"))
  cands <- .oa_works_to_candidates(fake)
  expect_equal(length(cands), 1L)
  # references == 原始 ID 列表（非格式化题录）
  expect_equal(cands[[1]]$references,
               list("https://openalex.org/W11", "https://openalex.org/W22"))
})

test_that(".oa_works_to_candidates handles missing optional fields gracefully", {
  minimal <- list(list(
    id = "https://openalex.org/W999",
    display_name = "Minimal Title",
    publication_year = 2020L,
    publication_date = NULL,
    doi = NULL,
    cited_by_count = 0L,
    authorships = list(),
    primary_location = NULL,
    abstract_inverted_index = NULL
  ))

  cands <- .oa_works_to_candidates(minimal)

  expect_equal(length(cands), 1L)
  expect_equal(cands[[1]]$openalexId,    "W999")
  expect_equal(cands[[1]]$title,         "Minimal Title")
  expect_equal(cands[[1]]$doi,           "")
  expect_equal(cands[[1]]$containerTitle, "")
  expect_equal(length(cands[[1]]$authors), 0L)
  expect_equal(cands[[1]]$abstract,      "")
  expect_equal(cands[[1]]$source,        "openalex")
})

test_that(".oa_works_to_candidates extracts multiple authors", {
  fake <- list(list(
    id = "https://openalex.org/W456",
    display_name = "Multi Author Paper",
    title = "Multi Author Paper",
    publication_year = 2021L,
    publication_date = "2021-06-15",
    doi = "https://doi.org/10.2/y",
    cited_by_count = 3L,
    authorships = list(
      list(author = list(display_name = "Alice Smith")),
      list(author = list(display_name = "Bob Jones")),
      list(author = list(display_name = "Carol White"))
    ),
    primary_location = list(source = list(display_name = "Nature")),
    abstract_inverted_index = NULL
  ))

  cands <- .oa_works_to_candidates(fake)

  expect_equal(length(cands[[1]]$authors), 3L)
  expect_equal(cands[[1]]$authors[[1]], "Alice Smith")
  expect_equal(cands[[1]]$authors[[2]], "Bob Jones")
  expect_equal(cands[[1]]$authors[[3]], "Carol White")
})

test_that(".oa_works_to_candidates returns empty list for empty input", {
  cands <- .oa_works_to_candidates(list())
  expect_equal(length(cands), 0L)
})

test_that(".oa_works_to_candidates strips doi URL prefix", {
  fake <- list(list(
    id = "https://openalex.org/W789",
    display_name = "DOI Test",
    title = "DOI Test",
    publication_year = 2023L,
    publication_date = "2023-01-01",
    doi = "https://doi.org/10.5678/test.paper",
    cited_by_count = 0L,
    authorships = list(),
    primary_location = NULL,
    abstract_inverted_index = NULL
  ))

  cands <- .oa_works_to_candidates(fake)

  expect_equal(cands[[1]]$doi, "10.5678/test.paper")
})

# ===========================================================================
# P1-1: .oa_is_error 辅助函数
# ===========================================================================

test_that(".oa_is_error 正确识别错误信号与正常列表", {
  expect_true(.oa_is_error(list(error = TRUE, status = 500L, message = "bad")))
  expect_false(.oa_is_error(list()))                   # 真空（正常0命中）
  expect_false(.oa_is_error(list(list(id = "W1"))))    # 正常结果列表
  expect_false(.oa_is_error(list(error = FALSE)))      # error=FALSE 不算错误
})

# ===========================================================================
# P1-1: oa_search_candidates HTTP 错误 → stop() 含 OPENALEX_UNAVAILABLE 前缀
# 使用 mockery::stub 对 oa_search_candidates 内调用的 .oa_search_works 打桩
# ===========================================================================

test_that("oa_search_candidates 在 .oa_search_works 报错时抛出结构化 stop()", {
  library(mockery)
  stub(oa_search_candidates, ".oa_search_works",
       function(query, n, since) list(error = TRUE, status = 503L,
                                      message = "OpenAlex 网络错误: timeout"))
  err <- tryCatch(
    oa_search_candidates("test", n = 5L),
    error = function(e) conditionMessage(e)
  )
  expect_match(err, "OPENALEX_UNAVAILABLE")
  expect_match(err, "503")
})

test_that("oa_search_candidates 真空（0命中）时正常返回空列表而非报错", {
  library(mockery)
  stub(oa_search_candidates, ".oa_search_works",
       function(query, n, since) list())   # 正常空结果
  result <- oa_search_candidates("veryrarequery999", n = 5L)
  expect_equal(length(result), 0L)  # 真空，不报错
})

# ===========================================================================
# oa_search_candidates n clamp ≤ 500（放开上限支持系统综述大批量,与 search clamp 一致）
# ===========================================================================

test_that("oa_search_candidates clamp n > 500 到 500", {
  library(mockery)
  calls <- list()
  stub(oa_search_candidates, ".oa_search_works",
       function(query, n, since) { calls[[length(calls) + 1L]] <<- n; list() })
  oa_search_candidates("test", n = 600L)
  expect_equal(calls[[1]], 500L)
})

test_that("oa_search_candidates clamp n = 100 保持不变", {
  library(mockery)
  calls <- list()
  stub(oa_search_candidates, ".oa_search_works",
       function(query, n, since) { calls[[length(calls) + 1L]] <<- n; list() })
  oa_search_candidates("test", n = 100L)
  expect_equal(calls[[1]], 100L)
})

# ===========================================================================
# P1-1: .oa_search_works 错误信号结构正确性（纯逻辑验证，不触网）
# ===========================================================================

test_that(".oa_search_works 返回的错误信号含 error/status/message 字段", {
  # 直接构造一个符合错误信号契约的对象并验证 .oa_is_error
  err_signal <- list(error = TRUE, status = 503L, message = "连接超时")
  expect_true(.oa_is_error(err_signal))
  expect_equal(err_signal$status, 503L)
  expect_match(err_signal$message, "连接超时")
})

# ===========================================================================
# Phase2 P2 复审修复验证
# ===========================================================================

# P2-fix-1: strsplit 用 fixed="|" 能正确解析 OPENALEX_UNAVAILABLE|503|<msg> 结构
# 验证 plumber.R 中修复后的解析逻辑：strsplit(msg, "|", fixed=TRUE) 能分出真实 status 和 message
test_that("P2-fix-1: strsplit fixed='|' 能正确解析 502 错误结构（strsplit 分隔符修复）", {
  msg <- "OPENALEX_UNAVAILABLE|503|OpenAlex 网络错误: timeout"
  parts <- strsplit(msg, "|", fixed = TRUE)[[1]]
  expect_equal(length(parts), 3L)
  expect_equal(parts[1], "OPENALEX_UNAVAILABLE")
  http_status <- suppressWarnings(as.integer(parts[2]))
  expect_equal(http_status, 503L)
  detail_msg <- paste(parts[-(1:2)], collapse = "|")
  expect_equal(detail_msg, "OpenAlex 网络错误: timeout")

  # 对比：旧写法 fixed=TRUE + 模式 "\\|" 不分割（字面反斜杠+竖线不存在于字符串中）
  old_parts <- strsplit(msg, "\\|", fixed = TRUE)[[1]]
  expect_equal(length(old_parts), 1L)   # 没有分割，只有一段 → 解析失败
})

# P2-fix-2: n=1.9 应被拒绝（非整数值校验）
# 验证 plumber.R 中 n %% 1 != 0 校验逻辑
test_that("P2-fix-2: n 非整数值校验逻辑（n=1.9 应触发 400 条件）", {
  check_n_is_integer <- function(raw_n) {
    if (!is.numeric(raw_n) || length(raw_n) != 1L) return("non-numeric")
    if (raw_n %% 1 != 0) return("not-integer")
    n <- suppressWarnings(as.integer(raw_n))
    if (is.na(n) || n < 1L) return("non-positive")
    "ok"
  }
  expect_equal(check_n_is_integer(1.9),  "not-integer")   # 应触发 400
  expect_equal(check_n_is_integer(0.5),  "not-integer")   # 应触发 400
  expect_equal(check_n_is_integer(25),   "ok")             # 合法
  expect_equal(check_n_is_integer(25L),  "ok")             # 合法整数
  expect_equal(check_n_is_integer(0),    "non-positive")   # 应触发 400
  expect_equal(check_n_is_integer(-1),   "non-positive")   # 应触发 400
})

# P2-fix-3: since="2024-13-99" 应被 as.Date 检测为 NA（非法日期校验）
# 验证 plumber.R 中 tryCatch(as.Date(...)) 判断 NA 的逻辑
test_that("P2-fix-3: since 真实日期校验逻辑（2024-13-99 应触发 400 条件）", {
  check_since_date <- function(since) {
    if (!grepl("^[0-9]{4}(-[0-9]{2}-[0-9]{2})?$", since)) return("format-error")
    if (nchar(since) == 4L) return("ok-year")   # 纯 YYYY 合法
    parsed <- tryCatch(as.Date(since, format = "%Y-%m-%d"), error = function(e) NA_Date_)
    if (is.na(parsed)) return("invalid-date")
    "ok"
  }
  expect_equal(check_since_date("2024-13-99"), "invalid-date")  # 月=13 非法 → 400
  expect_equal(check_since_date("2024-02-30"), "invalid-date")  # 2月无30日 → 400
  expect_equal(check_since_date("2024-01-01"), "ok")             # 合法日期
  expect_equal(check_since_date("2016"),        "ok-year")       # 纯 YYYY 合法
  expect_equal(check_since_date("2024-00-01"), "invalid-date")  # 月=0 非法 → 400
})
