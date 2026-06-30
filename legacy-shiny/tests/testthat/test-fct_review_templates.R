# R2-2: fct_review_templates.R 单测
source(file.path("..", "..", "R", "fct_review_templates.R"))

test_that("6 种论型全部可取且结构合规", {
  # v0.6 (spec §8): 新增 guidance (论型级写作指引) 与 chapter$focus (章节重点),
  # 用于注入抗幻觉 prompt; 结构契约相应扩展.
  for (k in c("undergrad", "master", "phd", "grant", "proposal", "sci_intro")) {
    t <- template_for(k)
    expect_named(t, c("name", "tone", "guidance", "chapters"), ignore.order = TRUE)
    expect_true(nzchar(t$name))
    expect_true(nzchar(t$tone))
    expect_true(is.character(t$guidance) && nzchar(t$guidance))
    expect_gte(length(t$chapters), 3L)
    for (ch in t$chapters) {
      expect_named(ch, c("title", "word_budget", "focus"), ignore.order = TRUE)
      expect_true(is.character(ch$title) && nzchar(ch$title))
      expect_true(is.character(ch$focus) && nzchar(ch$focus))
      expect_gte(as.integer(ch$word_budget), 100L)
    }
  }
})

test_that("抗幻觉硬约束常量存在且含关键约束", {
  expect_true(exists("REVIEW_GROUNDING_DIRECTIVE"))
  expect_true(grepl("抗幻觉", REVIEW_GROUNDING_DIRECTIVE))
  expect_true(grepl("严禁编造", REVIEW_GROUNDING_DIRECTIVE))
})

test_that("未知论型 stop", {
  expect_error(template_for("bogus"), "未知论型")
})

test_that("博士综述字数总和 >= 8000 (与字数档对齐期望)", {
  t <- template_for("phd")
  total <- sum(vapply(t$chapters, function(ch) as.integer(ch$word_budget),
                      integer(1)))
  expect_gte(total, 8000L)
})

test_that("SCI Intro 总字数控制在 900 左右 (与目标会议/期刊 Intro 长度对齐)", {
  t <- template_for("sci_intro")
  total <- sum(vapply(t$chapters, function(ch) as.integer(ch$word_budget),
                      integer(1)))
  expect_lt(total, 1500L)
  expect_gt(total, 500L)
})
