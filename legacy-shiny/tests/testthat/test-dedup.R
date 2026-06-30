# N1: fct_dedup.R 单测 — 去重 + 元数据修复
# 合成 data.frame 覆盖 4 种重复模式; enrich 用注入 mock, 不触真实网络.
source(file.path("..", "..", "R", "fct_crossref.R"))
source(file.path("..", "..", "R", "fct_dedup.R"))

# ---- 合成语料: DOI 完全重复 + 标题近似重复 + 无重复 + 无 DOI ----
# 行1/行2: 同一 DOI (大小写 + URL 前缀不同) → DOI 重复
# 行3: 独立
# 行5 (原件) / 行4: 无 DOI, 标题仅大小写+标点差异 (归一化后等同) 且同年 → 标题重复
# 行6: 无 DOI, 标题同 行4/5 但 *不同年* → 不应被去重
make_corpus <- function() {
  data.frame(
    AU = paste0("AUTHOR ", 1:6),
    TI = c(
      "DEEP LEARNING FOR BIBLIOMETRICS",                  # 1
      "DEEP LEARNING FOR BIBLIOMETRICS",                  # 2 (DOI 同上)
      "A COMPLETELY DIFFERENT STUDY ON CITATIONS",        # 3
      "Machine Learning for Scientometrics: A Review",    # 4 ~ 5 (大小写/标点)
      "MACHINE LEARNING FOR SCIENTOMETRICS A REVIEW",     # 5 (原件)
      "Machine Learning for Scientometrics, a Review!"    # 6 同 4/5 但异年
    ),
    PY = c(2020L, 2020L, 2021L, 2022L, 2022L, 2019L),
    DI = c(
      "10.1000/ABC",                       # 1
      "https://doi.org/10.1000/abc",       # 2 规范化后同 1
      "10.1000/xyz",                       # 3
      NA_character_, NA_character_, NA_character_  # 4,5,6 无 DOI
    ),
    AB = c("abs1", "abs2", "abs3", "", NA_character_, "abs6"),
    stringsAsFactors = FALSE
  )
}

# ---------------------------------------------------------------------------
# dedup_corpus
# ---------------------------------------------------------------------------

test_that("doi_first 仅按规范化 DOI 去重", {
  M <- make_corpus()
  res <- dedup_corpus(M, method = "doi_first")
  # 行2 (与行1 同 DOI) 被去; 标题近似的行4 因无 DOI 不动 → 6-1=5 篇
  expect_equal(nrow(res$corpus), 5L)
  expect_equal(sum(res$report$decision == "removed_dup_doi"), 1L)
  expect_equal(sum(res$report$decision == "removed_dup_title"), 0L)
  # 报告结构
  expect_s3_class(res$report, "data.frame")
  expect_equal(names(res$report),
               c("row", "decision", "matched_to", "basis", "similarity"))
  # 行2 决策与匹配
  r2 <- res$report[2, ]
  expect_equal(r2$decision, "removed_dup_doi")
  expect_equal(r2$matched_to, 1L)
  expect_equal(r2$basis, "doi")
  expect_equal(r2$similarity, 1)
})

test_that("doi_title 先 DOI 再标题+同年 模糊去重", {
  M <- make_corpus()
  res <- dedup_corpus(M, method = "doi_title", title_threshold = 0.92)
  # 去掉: 行2 (DOI) + 行5 (标题近似行4 且同年 2022, 保留首现行4) = 2 篇 → 4 篇
  # 行6 标题虽近似但 2019 != 2022, 不去
  expect_equal(nrow(res$corpus), 4L)
  expect_equal(sum(res$report$decision == "removed_dup_doi"), 1L)
  expect_equal(sum(res$report$decision == "removed_dup_title"), 1L)
  # 保留策略 = 首次出现; 行4 是原件 (kept), 行5 被去并匹配回行4
  r5 <- res$report[5, ]
  expect_equal(r5$decision, "removed_dup_title")
  expect_equal(r5$matched_to, 4L)
  expect_equal(r5$basis, "title+year")
  expect_gte(r5$similarity, 0.92)
  expect_equal(res$report[4, "decision"], "kept")
  # 行6 必须保留 (异年)
  expect_equal(res$report[6, "decision"], "kept")
})

test_that("title_threshold 参数控制模糊判重的松紧", {
  # 专用合成对: 标题归一化后非完全相同 (单复数差一字母),
  # JW 相似度约 0.915 — 介于 0.90 与 0.92 之间, 用于卡阈值边界.
  M <- data.frame(
    AU = c("X", "Y"),
    TI = c("MACHINE LEARNING APPROACHES TO SCIENTOMETRICS",
           "MACHINE LEARNING APPROACH TO SCIENTOMETRICS"),
    PY = c(2022L, 2022L),
    DI = c(NA_character_, NA_character_),
    stringsAsFactors = FALSE
  )
  # 阈值 0.92: 0.915 < 0.92 → 不判重, 2 篇
  loose <- dedup_corpus(M, method = "doi_title", title_threshold = 0.92)
  expect_equal(nrow(loose$corpus), 2L)
  expect_equal(sum(loose$report$decision == "removed_dup_title"), 0L)
  # 阈值 0.90: 0.915 >= 0.90 → 判重, 1 篇
  tight <- dedup_corpus(M, method = "doi_title", title_threshold = 0.90)
  expect_equal(nrow(tight$corpus), 1L)
  expect_equal(sum(tight$report$decision == "removed_dup_title"), 1L)
})

test_that("无重复语料原样返回", {
  M <- make_corpus()[c(1, 3, 5), ]  # 三条互不重复
  rownames(M) <- NULL
  res <- dedup_corpus(M, method = "doi_title")
  expect_equal(nrow(res$corpus), 3L)
  expect_true(all(res$report$decision == "kept"))
})

# ---- 边界 ----
test_that("空 corpus 优雅返回空报告", {
  empty <- make_corpus()[0, ]
  res <- dedup_corpus(empty)
  expect_equal(nrow(res$corpus), 0L)
  expect_equal(nrow(res$report), 0L)
  expect_s3_class(res$report, "data.frame")
})

test_that("NULL / 非 data.frame 不崩溃", {
  expect_silent(r1 <- dedup_corpus(NULL))
  expect_null(r1$corpus)
  expect_equal(nrow(r1$report), 0L)
  expect_silent(r2 <- dedup_corpus("not a df"))
  expect_equal(r2$corpus, "not a df")
})

test_that("单行 corpus 必保留", {
  M <- make_corpus()[1, ]
  res <- dedup_corpus(M, method = "doi_title")
  expect_equal(nrow(res$corpus), 1L)
  expect_equal(res$report$decision, "kept")
})

test_that("全无 DOI: doi_title 走标题, doi_first 全留", {
  M <- make_corpus()[4:6, ]  # 三条均无 DOI
  rownames(M) <- NULL
  # doi_first: 无 DOI 全留 → 3
  expect_equal(nrow(dedup_corpus(M, method = "doi_first")$corpus), 3L)
  # doi_title: 行4(原3)~行5(原4)? 重排后 1&2 同年同标题 → 去 1 条 → 2
  # 重排后第1/2行同标题同年(2022) → 去 1 条; 第3行(2019)异年保留 → 2 篇
  res <- dedup_corpus(M, method = "doi_title")
  expect_equal(nrow(res$corpus), 2L)
})

test_that("缺 DI 列时按无 DOI 处理, 不报错", {
  M <- make_corpus()
  M$DI <- NULL
  expect_silent(res <- dedup_corpus(M, method = "doi_first"))
  expect_equal(nrow(res$corpus), 6L)  # 无 DOI 列 → 全保留
})

# ---------------------------------------------------------------------------
# enrich_metadata (mock 注入, 不触网络)
# ---------------------------------------------------------------------------

test_that("enrich 用 mock 回填缺失 DI 与 AB", {
  M <- make_corpus()
  # 制造缺失: 行4 缺 DI(本就 NA) 缺 AB(""); 给个可反查的标题映射
  fake_lookup <- function(title, ...) {
    if (grepl("Scientometrics", title, ignore.case = TRUE)) {
      list(doi = "10.9999/filled", abstract = "filled abstract text")
    } else {
      list(doi = NA_character_, abstract = NA_character_)
    }
  }
  res <- enrich_metadata(M, targets = c("DI", "AB"),
                         lookup_fn = fake_lookup)
  # 行4 (Machine Learning for Scientometrics, 无 DI 无 AB) 应被补全
  expect_equal(res$corpus$DI[4], "10.9999/filled")
  expect_equal(res$corpus$AB[4], "filled abstract text")
  # 报告含两条 (DI + AB), 结构正确
  expect_s3_class(res$report, "data.frame")
  expect_equal(names(res$report), c("row", "field", "source", "value"))
  expect_true(all(c("DI", "AB") %in% res$report$field))
  expect_true(all(res$report$source == "crossref"))
})

test_that("enrich 不覆盖已有字段", {
  M <- make_corpus()
  # 行1 已有 DI 和 AB, mock 即使返回值也不应改写
  fake_lookup <- function(title, ...) {
    list(doi = "10.0000/should_not_apply",
         abstract = "should not apply")
  }
  res <- enrich_metadata(M, targets = c("DI", "AB"),
                         lookup_fn = fake_lookup)
  expect_equal(res$corpus$DI[1], "10.1000/ABC")
  expect_equal(res$corpus$AB[1], "abs1")
  expect_false(any(res$report$row == 1L))
})

test_that("enrich max_lookup 限流生效", {
  M <- make_corpus()
  calls <- 0L
  counting_lookup <- function(title, ...) {
    calls <<- calls + 1L
    list(doi = "10.1/x", abstract = "x")
  }
  enrich_metadata(M, targets = c("DI", "AB"),
                  max_lookup = 1L, lookup_fn = counting_lookup)
  expect_lte(calls, 1L)
})

test_that("enrich lookup 抛错时逐条降级不中断", {
  M <- make_corpus()
  boom <- function(title, ...) stop("network down")
  expect_silent(res <- enrich_metadata(M, lookup_fn = boom))
  expect_equal(nrow(res$report), 0L)        # 无补全
  expect_equal(nrow(res$corpus), nrow(M))    # corpus 原样
})

test_that("enrich 空 / 无 TI 列优雅返回", {
  expect_equal(nrow(enrich_metadata(NULL)$report), 0L)
  empty <- make_corpus()[0, ]
  expect_equal(nrow(enrich_metadata(empty)$report), 0L)
  noti <- make_corpus(); noti$TI <- NULL
  res <- enrich_metadata(noti)
  expect_equal(nrow(res$report), 0L)
  expect_equal(res$corpus, noti)
})

# ---- L3: 真实 Crossref 网络 (默认跳过) ----
test_that("L3 真实 Crossref 标题反查 DOI (RUN_LIVE_LLM=true 才跑)", {
  testthat::skip_if_not(Sys.getenv("RUN_LIVE_LLM") == "true",
                        "需 RUN_LIVE_LLM=true")
  testthat::skip_if_offline()
  res <- .crossref_by_title("bibliometrix An R-tool for comprehensive science mapping analysis")
  expect_true(is.list(res))
  expect_true(!is.na(res$doi))
})
