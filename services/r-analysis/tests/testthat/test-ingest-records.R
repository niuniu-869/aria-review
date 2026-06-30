# test-ingest-records.R — 题录入库 keywords→DE 归一化回归测试
#
# 背景: "R 分析一直分析不出关键词" 的根因是上游关键词分隔符不统一
# (OpenAlex 用分号, PDF/手动/Sciverse 常用逗号, LLM 可能返回列表), 而 bibliometrix
# 全链路按 ";" 切分 DE。未归一时逗号串会塌缩成单个关键词、列表只保留首项。
# 本测试锁定 .normalize_keywords 的归一行为, 并端到端验证 documents_dto 词频可用。

# ---- 单元: .normalize_keywords 各形态归一 ----

test_that("逗号分隔串 → 拆成多个关键词 (核心 bug)", {
  expect_equal(.normalize_keywords("machine learning, deep learning, NLP"),
               "MACHINE LEARNING;DEEP LEARNING;NLP")
})

test_that("分号分隔串 → 保持 (文献计量标准)", {
  expect_equal(.normalize_keywords("IPO; text mining; sentiment"),
               "IPO;TEXT MINING;SENTIMENT")
})

test_that("竖线 / 中文顿号 / 中文分号 均可切分", {
  expect_equal(.normalize_keywords("a|b|c"), "A;B;C")
  expect_equal(.normalize_keywords("甲、乙、丙"), "甲;乙;丙")
  expect_equal(.normalize_keywords("x；y；z"), "X;Y;Z")
})

test_that("列表/向量输入 → 全部保留 (修复仅取首项的旧 bug)", {
  expect_equal(.normalize_keywords(list("IPO", "text mining", "finance")),
               "IPO;TEXT MINING;FINANCE")
  expect_equal(.normalize_keywords(c("a", "b")), "A;B")
})

test_that("分号优先于逗号 (混合分隔时不误切关键词内部逗号)", {
  expect_equal(.normalize_keywords("survey, a; method"), "SURVEY, A;METHOD")
})

test_that("去空 / 去重 / 去首尾空白", {
  expect_equal(.normalize_keywords("  a ,, b , a "), "A;B")
  expect_equal(.normalize_keywords(";;"), "")
})

test_that("空 / NULL / NA → 空串", {
  expect_equal(.normalize_keywords(NULL), "")
  expect_equal(.normalize_keywords(""), "")
  expect_equal(.normalize_keywords(NA_character_), "")
  expect_equal(.normalize_keywords(list()), "")
})

# ---- 端到端: 逗号关键词题录 → DE 多词 → documents_dto 词频可用 ----

.mk_records_comma_kw <- function() {
  list(
    list(title = "Paper A", year = 2021, keywords = "machine learning, IPO, finance",
         creators = list(list(family = "Smith", given = "John"))),
    list(title = "Paper B", year = 2022, keywords = "machine learning, NLP",
         creators = list(list(family = "Doe", given = "Jane"))),
    list(title = "Paper C", year = 2023, keywords = "finance, IPO",
         creators = list(list(family = "Lee", given = "Amy")))
  )
}

test_that("records_to_bib_df: 逗号关键词 → DE 为分号串", {
  df <- records_to_bib_df(.mk_records_comma_kw())
  expect_true(all(grepl(";", df$DE)))                 # 已归一为分号
  expect_false(any(grepl(",", df$DE)))                # 不再残留逗号
  # Paper A 三个关键词
  expect_equal(length(strsplit(df$DE[1], ";", fixed = TRUE)[[1]]), 3L)
})

test_that("documents_dto: 逗号关键词语料能算出多个关键词词频 (回归)", {
  df <- records_to_bib_df(.mk_records_comma_kw())
  dto <- documents_dto(df, top = 20L)
  terms <- vapply(dto$keywords, function(k) k$term, character(1))
  # 修复前: 整串 "MACHINE LEARNING, IPO, FINANCE" 会是单个 term → 这里应 >= 4 个去重词
  expect_gte(length(terms), 4L)
  expect_true("MACHINE LEARNING" %in% terms)
  expect_true("IPO" %in% terms)
  # MACHINE LEARNING 出现在 A、B 两篇 → 频次 2
  ml <- Filter(function(k) k$term == "MACHINE LEARNING", dto$keywords)[[1]]
  expect_equal(ml$freq, 2L)
})

test_that("keyword_trend_dto: 逗号关键词语料历时演变 available", {
  df <- records_to_bib_df(.mk_records_comma_kw())
  e <- keyword_trend_dto(df, top_terms = 10L)
  expect_true(is.null(e$available) || isTRUE(e$available))
  expect_true(length(e$data$terms %||% e$terms) >= 3L)
})

# ---- records 语料必须带 SR, 否则 bibliometrix 全链路落空 ----

.mk_records_network <- function() {
  kws <- c("machine learning;IPO;finance", "machine learning;NLP;finance",
           "IPO;valuation;finance", "NLP;deep learning;IPO",
           "machine learning;deep learning;IPO", "finance;valuation;risk")
  lapply(1:18, function(i) list(
    title = paste("Paper", i), year = 2018 + (i %% 6),
    keywords = kws[[((i - 1) %% 6) + 1]],
    container_title = paste("Journal", i %% 4),
    creators = list(list(family = paste0("Sur", i %% 7), given = "X"),
                    list(family = paste0("Co", i %% 3), given = "Y"))))
}

test_that("records_to_bib_df 生成唯一 SR 列 (bibliometrix 文档主键)", {
  df <- records_to_bib_df(.mk_records_network())
  expect_true("SR" %in% names(df))
  expect_equal(length(unique(df$SR)), nrow(df))   # 唯一
  expect_false(any(is.na(df$SR)))
})

test_that("conceptual_dto: records 语料关键词共现网络有节点和边 (回归)", {
  df <- records_to_bib_df(.mk_records_network())
  g <- conceptual_dto(df, n = 30L)$graph
  expect_gt(length(g$nodes), 0L)   # 修复前缺 ID 列 → "undefined columns" → 空
  expect_gt(length(g$edges), 0L)
})

test_that("threefield_dto: records 语料 作者→关键词→来源 Sankey 可用", {
  df <- records_to_bib_df(.mk_records_network())
  tf <- threefield_dto(df)
  expect_true(is.null(tf$available) || isTRUE(tf$available))
  expect_gt(length(tf$data$nodes %||% tf$nodes), 0L)
})
