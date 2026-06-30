test_that("overview_dto 契约对齐 + JSON 可序列化", {
  data(scientometrics, package = "bibliometrixData")
  dto <- overview_dto(scientometrics)

  expect_equal(dto$schemaVersion, 1L)
  req_fields <- c("documents", "sources", "authors",
                  "avgCitationsPerDoc", "timespanFrom", "timespanTo")
  expect_true(all(req_fields %in% names(dto$stats)))
  expect_equal(dto$stats$documents, nrow(scientometrics))
  expect_gt(dto$stats$authors, 0)
  expect_gt(dto$stats$sources, 0)
  expect_lte(dto$stats$timespanFrom, dto$stats$timespanTo)
  expect_gt(length(dto$annualProduction), 0)

  # 年度产出每篇一年, 总和应 <= 文档数 (部分 PY 可能缺)
  total <- sum(vapply(dto$annualProduction, function(p) p$articles, integer(1)))
  expect_lte(total, dto$stats$documents)

  # JSON 可序列化 + round-trip
  j <- jsonlite::toJSON(dto, auto_unbox = TRUE, null = "null")
  expect_true(jsonlite::validate(j))
  back <- jsonlite::fromJSON(j, simplifyVector = TRUE)
  expect_equal(back$stats$documents, dto$stats$documents)
})

test_that("overview_dto 空语料 / 无 PY 报错", {
  expect_error(overview_dto(data.frame()), "空")
  expect_error(
    overview_dto(data.frame(AU = "X", SO = "Y", stringsAsFactors = FALSE)),
    "PY"
  )
})

test_that("new_corpus_id 形如 uuid v4", {
  expect_match(
    new_corpus_id(),
    "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
  )
})

test_that("save/load corpus roundtrip + 状态机", {
  data(scientometrics, package = "bibliometrixData")
  id <- new_corpus_id()
  meta <- save_corpus(scientometrics, id, "wos", status = "ready")
  expect_equal(meta$status, "ready")
  expect_equal(meta$documentCount, nrow(scientometrics))

  m2 <- load_corpus_meta(id)
  expect_equal(m2$status, "ready")
  M2 <- load_corpus(id)
  expect_equal(nrow(M2), nrow(scientometrics))
})

test_that("parse_and_store 坏文件 → failed 且脱敏, 不存语料", {
  bad <- tempfile(fileext = ".txt")
  writeLines("this is not a wos export", bad)
  meta <- parse_and_store(bad, "wos")
  expect_equal(meta$status, "failed")
  expect_match(meta$error, "解析失败")
  expect_null(load_corpus(meta$corpusId))
  unlink(bad)
})

test_that("load_corpus_meta 不存在返回 NULL", {
  expect_null(load_corpus_meta("does-not-exist-id"))
})

test_that("非法 corpus_id 被拒 (防路径遍历 + RDS, Codex step2-P1)", {
  expect_false(.is_valid_id("../../etc/passwd"))
  expect_false(.is_valid_id("not-a-uuid"))
  expect_null(load_corpus_meta("../../etc/passwd"))
  expect_null(load_corpus("../../secret"))
  expect_error(save_corpus(data.frame(x = 1), "../evil", "wos"), "非法")
})

test_that("corpus_records 返回文献列表 (供 grounding)", {
  data(scientometrics, package = "bibliometrixData")
  recs <- corpus_records(scientometrics, limit = 5)
  expect_length(recs, 5)
  expect_true(all(vapply(recs, function(r) is.integer(r$idx), logical(1))))
  expect_true("title" %in% names(recs[[1]]))
  # JSON 可序列化
  expect_true(jsonlite::validate(jsonlite::toJSON(recs, auto_unbox = TRUE, null = "null")))
  # 空语料 → 空 list
  expect_length(corpus_records(data.frame()), 0)
  # limit 截断
  expect_length(corpus_records(scientometrics, limit = 3), 3)
})

test_that("avgCitationsPerDoc 分母为全部文档, 缺 TC 记 0 (Codex step2-P1)", {
  M <- data.frame(
    AU = c("A", "B", "C"), SO = c("J1", "J1", "J2"),
    PY = c(2020L, 2021L, 2021L), TC = c(10, NA, 2),
    stringsAsFactors = FALSE
  )
  dto <- overview_dto(M)
  expect_equal(dto$stats$documents, 3L)
  expect_equal(dto$stats$avgCitationsPerDoc, round((10 + 0 + 2) / 3, 4))
  expect_equal(dto$stats$sources, 2L)
  expect_equal(dto$stats$authors, 3L)
})
