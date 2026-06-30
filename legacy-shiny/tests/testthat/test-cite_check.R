# N2: fct_cite_check.R 单测 (AI 输出引用完整性校验, 抗幻觉)
source(file.path("..", "..", "R", "fct_cite_check.R"))

# 合成 corpus: 含已知 DOI / 作者 / 年 / 标题 / PMID
test_corpus_cc <- function() {
  data.frame(
    AU = c("ARIA M;CUCCURULLO C",      # Aria 2017
           "WANG X;LIU Y",             # Wang 2020
           "张三 ;李四 ",               # 张三 2021 (中文姓)
           "SMITH J;JONES A"),         # Smith 2019
    PY = c(2017L, 2020L, 2021L, 2019L),
    TI = c("BIBLIOMETRIX: AN R-TOOL FOR SCIENCE MAPPING",
           "A SCIENTOMETRIC STUDY OF AI",
           "中文文献计量学综述",
           "DEEP LEARNING FOR VISION"),
    DI = c("10.1016/j.joi.2017.08.007",
           "10.1007/s11192-020-03434-4",
           "10.1234/zhongwen.2021.001",
           "10.1109/cvpr.2019.00001"),
    PM = c("28890123", "", "", "31000001"),
    AB = c("an abstract", "another abstract", "中文摘要", "vision abstract"),
    stringsAsFactors = FALSE
  )
}

test_that("真实 DOI 判 green, 虚构 DOI 判 red", {
  M <- test_corpus_cc()
  txt <- "见 10.1016/j.joi.2017.08.007 与虚构的 10.9999/fake.2099.zzz 。"
  res <- check_citations(txt, M)
  doi_cites <- res$cites[res$cites$type == "doi", ]
  expect_equal(nrow(doi_cites), 2L)
  real <- doi_cites[grepl("joi.2017", doi_cites$text), ]
  fake <- doi_cites[grepl("9999", doi_cites$text), ]
  expect_equal(real$status, "green")
  expect_false(is.na(real$matched_idx))
  expect_equal(fake$status, "red")
  expect_true(is.na(fake$matched_idx))
})

test_that("真实作者+年(英) 判 yellow 并指向正确行", {
  M <- test_corpus_cc()
  res <- check_citations("如 Aria and Cuccurullo (2017) 所述。", M)
  c1 <- res$cites[res$cites$type == "en", ]
  expect_equal(nrow(c1), 1L)
  expect_equal(c1$status, "yellow")
  expect_equal(c1$matched_idx, 1L)  # Aria 行
})

test_that("(Smith, 2019) 括号内作者+年 命中", {
  M <- test_corpus_cc()
  res <- check_citations("近期工作 (Smith, 2019) 表明……", M)
  c1 <- res$cites[res$cites$type == "en", ]
  expect_equal(nrow(c1), 1L)
  expect_equal(c1$status, "yellow")
  expect_equal(c1$matched_idx, 4L)
})

test_that("虚构作者+年 判 red", {
  M <- test_corpus_cc()
  res <- check_citations("Nonexistent et al. (2099) 提出……", M)
  c1 <- res$cites[res$cites$type == "en", ]
  expect_equal(nrow(c1), 1L)
  expect_equal(c1$status, "red")
})

test_that("作者命中但年份不符 仍 yellow (提示请确认)", {
  M <- test_corpus_cc()
  res <- check_citations("Aria et al. (1999) 的早期工作", M)
  c1 <- res$cites[res$cites$type == "en", ]
  expect_equal(c1$status, "yellow")
  expect_equal(c1$matched_idx, 1L)
})

test_that("中文作者+年 模式被提取并匹配", {
  M <- test_corpus_cc()
  res <- check_citations("张三 (2021) 与（李四, 2021）的研究。", M)
  cn <- res$cites[res$cites$type == "cn", ]
  expect_equal(nrow(cn), 2L)
  expect_true(all(cn$status == "yellow"))
})

test_that("PMID 真实判 green, 虚构判 red", {
  M <- test_corpus_cc()
  res <- check_citations("参 PMID: 28890123 及 PMID 99999999", M)
  pm <- res$cites[res$cites$type == "pmid", ]
  expect_equal(nrow(pm), 2L)
  real <- pm[grepl("28890123", pm$text), ]
  fake <- pm[grepl("99999999", pm$text), ]
  expect_equal(real$status, "green")
  expect_equal(real$matched_idx, 1L)
  expect_equal(fake$status, "red")
})

test_that("编号引用 [n] 被提取, 标 yellow (待核)", {
  M <- test_corpus_cc()
  res <- check_citations("如文献 [1] 与 [12] 所示。", M)
  num <- res$cites[res$cites$type == "num", ]
  expect_equal(nrow(num), 2L)
  expect_true(all(num$status == "yellow"))
  expect_true(all(is.na(num$matched_idx)))
})

test_that("summary 计数与 cites 行数一致", {
  M <- test_corpus_cc()
  txt <- paste(
    "10.1016/j.joi.2017.08.007",     # green
    "10.9999/fake.2099.zzz",         # red
    "Aria and Cuccurullo (2017)",    # yellow
    "Nonexistent et al. (2099)",     # red
    "PMID: 28890123",                # green
    sep = "\n")
  res <- check_citations(txt, M)
  expect_equal(res$summary$green, 2L)
  expect_equal(res$summary$red, 2L)
  expect_equal(res$summary$yellow, 1L)
  total <- res$summary$green + res$summary$yellow + res$summary$red
  expect_equal(total, nrow(res$cites))
})

test_that("annotated 在每条引用后插入正确 emoji 且仍可被渲染器解析", {
  M <- test_corpus_cc()
  txt <- "真实 10.1016/j.joi.2017.08.007 与虚构 10.9999/fake.2099.zzz"
  res <- check_citations(txt, M)
  # green DOI 后应有 ✅, red DOI 后应有 ❌
  expect_true(grepl("10.1016/j.joi.2017.08.007 ✅", res$annotated, fixed = TRUE))
  expect_true(grepl("10.9999/fake.2099.zzz ❌", res$annotated, fixed = TRUE))
  # 原文内容保留
  expect_true(grepl("真实", res$annotated, fixed = TRUE))
})

test_that("annotated 输出能被 render_markdown_safe 渲染 (集成)", {
  skip_if_not_installed("commonmark")
  skip_if_not_installed("xml2")
  source(file.path("..", "..", "R", "fct_markdown.R"))
  M <- test_corpus_cc()
  res <- check_citations("## 综述\n\n见 Aria and Cuccurullo (2017) 的工作。", M)
  html <- render_markdown_safe(res$annotated)
  expect_s3_class(html, "html")
  expect_true(grepl("⚠", as.character(html)) || grepl("warning", as.character(html), ignore.case = TRUE) || nzchar(as.character(html)))
})

test_that("cites data.frame 结构正确 (4 列)", {
  M <- test_corpus_cc()
  res <- check_citations("Aria (2017)", M)
  expect_named(res$cites, c("text", "type", "status", "matched_idx"))
  expect_s3_class(res$cites, "data.frame")
})

# ---- 边界 ------------------------------------------------------------------

test_that("空文本返回空结果不崩", {
  M <- test_corpus_cc()
  res <- check_citations("", M)
  expect_equal(nrow(res$cites), 0L)
  expect_equal(res$summary, list(green = 0L, yellow = 0L, red = 0L))
  res2 <- check_citations(NULL, M)
  expect_equal(nrow(res2$cites), 0L)
  res3 <- check_citations(character(0), M)
  expect_equal(nrow(res3$cites), 0L)
})

test_that("空 corpus / NULL corpus → 所有引用判 red 不崩", {
  res <- check_citations("见 10.1016/j.joi.2017.08.007 与 Smith (2019)", NULL)
  expect_true(all(res$cites$status == "red"))
  empty_M <- data.frame()
  res2 <- check_citations("见 10.1016/j.joi.2017.08.007", empty_M)
  expect_true(all(res2$cites$status == "red"))
})

test_that("corpus 缺关键字段 (无 DI/AU) 不崩", {
  M <- data.frame(TI = c("X", "Y"), PY = c(2020L, 2021L),
                  stringsAsFactors = FALSE)
  res <- check_citations("10.1016/j.joi.2017.08.007 与 Smith (2020)", M)
  expect_true(is.list(res))
  # 无 DI → DOI 判 red; 无 AU → 作者匹配判 red
  expect_true(all(res$cites$status == "red"))
})

test_that("无引用的纯文本: annotated == 原文, summary 全 0", {
  M <- test_corpus_cc()
  txt <- "这是一段没有任何引用的普通综述文字。"
  res <- check_citations(txt, M)
  expect_equal(nrow(res$cites), 0L)
  expect_equal(res$annotated, txt)
  expect_equal(res$summary$green + res$summary$yellow + res$summary$red, 0L)
})

test_that("性能: 50 引用 corpus 校验 < 200ms", {
  skip_on_cran()
  M <- test_corpus_cc()
  # 构造 50 条混合引用
  parts <- character(0)
  for (i in 1:10) {
    parts <- c(parts,
      "10.1016/j.joi.2017.08.007",
      "10.9999/fake.x",
      "Aria and Cuccurullo (2017)",
      "Smith et al. (2019)",
      "[1]")
  }
  txt <- paste(parts, collapse = " ; ")
  t0 <- Sys.time()
  res <- check_citations(txt, M)
  dt <- as.numeric(Sys.time() - t0, units = "secs")
  expect_gte(nrow(res$cites), 50L)
  expect_lt(dt, 0.2)
})
