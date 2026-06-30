# OpenAlex 接入纯函数测试 (不触网; 网络路径由 live smoke 验证)

test_that("摘要倒排索引按位置还原原文", {
  inv <- list("Bibliometric" = list(0), "analysis" = list(1, 4),
              "of" = list(2), "papers" = list(3), "again" = list(5))
  expect_equal(.oa_inverted_to_abstract(inv),
               "Bibliometric analysis of papers analysis again")
  expect_equal(.oa_inverted_to_abstract(NULL), "")
  expect_equal(.oa_inverted_to_abstract(list()), "")
})

test_that("标题相似度: 仅大小写/标点差异≈1, 无关标题≈0", {
  expect_equal(.oa_title_sim("Textual Analysis in Accounting",
                             "Textual analysis in accounting"), 1)
  expect_lt(.oa_title_sim("Foo bar baz", "Quantum gravity loops"), 0.2)
  expect_equal(.oa_title_sim("", "x"), 0)
})

test_that("文本清洗去 HTML 标签/实体/控制字符", {
  expect_equal(.oa_clean_text("A <i>study</i> of&amp;more"), "A study of more")
  expect_equal(.oa_clean_text(NULL), "")
})

test_that("第一作者短形 LASTNAME FI", {
  au <- list(list(author = list(display_name = "John David Smith")))
  expect_equal(.oa_first_author_short(au), "SMITH JD")
  expect_equal(.oa_first_author_short(list()), "ANON")
})

test_that("单 work 组装 WoS 块并被 convert2df 解析", {
  work <- list(
    id = "https://openalex.org/W777",
    title = "A Study of <i>IPO</i> Prospectus Textual Analysis",
    publication_year = 2021L, cited_by_count = 42L,
    doi = "https://doi.org/10.1000/abc",
    primary_location = list(source = list(display_name = "Journal of Finance",
                                          issn_l = "0022-1082")),
    authorships = list(
      list(author = list(display_name = "John Smith"),
           institutions = list(list(display_name = "MIT"))),
      list(author = list(display_name = "Alice Brown"), institutions = list())),
    abstract_inverted_index = list("We" = list(0), "study" = list(1), "IPOs" = list(2)),
    keywords = list(list(display_name = "IPO"), list(display_name = "text mining")),
    biblio = list(volume = "76", issue = "3", first_page = "100", last_page = "120"),
    referenced_works = list())

  blk <- .oa_work_to_wos_block(work)
  expect_match(blk, "AU SMITH J")
  expect_match(blk, "TI A STUDY OF IPO PROSPECTUS TEXTUAL ANALYSIS")  # HTML 已清
  expect_match(blk, "UT WOS:W777")
  expect_match(blk, "DI 10.1000/abc")                                  # DOI 前缀去除

  path <- .oa_works_to_wos_file(list(work), with_refs = FALSE)
  on.exit(unlink(path), add = TRUE)
  M <- bibliometrix::convert2df(file = path, dbsource = "wos", format = "plaintext")
  expect_equal(nrow(M), 1L)
  expect_true(all(c("AU", "TI", "SO", "PY", "TC") %in% names(M)))
  expect_equal(as.integer(M$PY[1]), 2021L)
})

test_that("空输入安全降级", {
  expect_null(.oa_works_to_wos_file(list()))
  res <- oa_build_wos_from_papers(list())
  expect_null(res$path)
  expect_equal(res$matched, 0L)
})

test_that("papers 为 data.frame 时归一化不报错 (空题录离线降级, 不触网)", {
  df <- data.frame(title = "", doi = "", stringsAsFactors = FALSE)
  res <- oa_build_wos_from_papers(df)   # 空 title/doi → 直接 unmatched, 无网络调用
  expect_null(res$path)
  expect_equal(length(res$unmatched), 1L)
})
