# test-pubmed.R — F4 PubMed 接入单测 (spec §4.5)
#
# 分层:
#   L1 (无网络, 必跑): nbib_parse / .parse_efetch_xml 用合成数据验证字段映射,
#                       valid_corpus, PM/UT 列, 边界 (空输入/空结果).
#   L3 (真实网络, skip): pubmed_to_corpus 真调 NCBI, 默认 skip_if_offline.
#
# import_corpus 由 helper-fixtures.R 经 fct_analysis.R 提供; 这里再 source 被测文件.
source(file.path("..", "..", "R", "fct_pubmed.R"))

# 合成 .nbib (MEDLINE 格式) ----------------------------------------------------
.synthetic_nbib <- function() {
  paste(
    "PMID- 12345678",
    "OWN - NLM",
    "TI  - Machine learning for cancer diagnosis: a systematic review.",
    "AB  - This study reviews machine learning methods for oncology.",
    "FAU - Smith, John A",
    "AU  - Smith JA",
    "AD  - Department of Oncology, Harvard University, Boston, MA, USA.",
    "FAU - Doe, Jane",
    "AU  - Doe J",
    "AD  - Department of CS, MIT, Cambridge, MA, USA.",
    "JT  - Journal of Medical Informatics",
    "TA  - J Med Inform",
    "DP  - 2023 Jun",
    "MH  - Neoplasms/diagnosis",
    "MH  - Machine Learning",
    "PT  - Journal Article",
    "PT  - Review",
    "LID - 10.1000/jmi.2023.001 [doi]",
    "AID - 10.1000/jmi.2023.001 [doi]",
    "VI  - 45",
    "IP  - 3",
    "PG  - 100-110",
    "SO  - J Med Inform. 2023 Jun;45(3):100-110.",
    "",
    "PMID- 87654321",
    "TI  - Deep learning in radiology.",
    "AB  - A concise review of deep learning applications.",
    "FAU - Lee, Kevin",
    "AU  - Lee K",
    "AD  - Radiology Dept, Stanford, CA, USA.",
    "JT  - Radiology Today",
    "TA  - Radiol Today",
    "DP  - 2024",
    "MH  - Radiology",
    "PT  - Journal Article",
    "AID - 10.2000/rt.2024.5 [doi]",
    "SO  - Radiol Today. 2024;10(1):5.",
    sep = "\n"
  )
}

# 合成 efetch XML (PubmedArticleSet) ------------------------------------------
.synthetic_efetch_xml <- function() {
  paste0(
    '<?xml version="1.0"?>',
    '<PubmedArticleSet>',
    '<PubmedArticle><MedlineCitation>',
    '<PMID>12345678</PMID>',
    '<Article>',
    '<Journal><ISSN>1234-5678</ISSN>',
    '<JournalIssue><Volume>45</Volume><Issue>3</Issue>',
    '<PubDate><Year>2023</Year><Month>Jun</Month></PubDate></JournalIssue>',
    '<Title>Journal of Medical Informatics</Title>',
    '<ISOAbbreviation>J Med Inform</ISOAbbreviation></Journal>',
    '<ArticleTitle>Machine learning for cancer diagnosis: a systematic review.</ArticleTitle>',
    '<Pagination><MedlinePgn>100-110</MedlinePgn></Pagination>',
    '<Abstract><AbstractText>This study reviews machine learning methods for oncology.</AbstractText></Abstract>',
    '<AuthorList>',
    '<Author><LastName>Smith</LastName><ForeName>John A</ForeName><Initials>JA</Initials>',
    '<AffiliationInfo><Affiliation>Department of Oncology, Harvard University, Boston, MA, USA.</Affiliation></AffiliationInfo></Author>',
    '<Author><LastName>Doe</LastName><ForeName>Jane</ForeName><Initials>J</Initials>',
    '<AffiliationInfo><Affiliation>Department of CS, MIT, Cambridge, MA, USA.</Affiliation></AffiliationInfo></Author>',
    '</AuthorList>',
    '<ELocationID EIdType="doi">10.1000/jmi.2023.001</ELocationID>',
    '<PublicationTypeList><PublicationType>Journal Article</PublicationType>',
    '<PublicationType>Review</PublicationType></PublicationTypeList>',
    '</Article>',
    '<MeshHeadingList>',
    '<MeshHeading><DescriptorName>Neoplasms</DescriptorName></MeshHeading>',
    '<MeshHeading><DescriptorName>Machine Learning</DescriptorName></MeshHeading>',
    '</MeshHeadingList>',
    '<MedlineJournalInfo><MedlineTA>J Med Inform</MedlineTA></MedlineJournalInfo>',
    '</MedlineCitation>',
    '<PubmedData><ArticleIdList>',
    '<ArticleId IdType="pubmed">12345678</ArticleId>',
    '<ArticleId IdType="doi">10.1000/jmi.2023.001</ArticleId>',
    '</ArticleIdList></PubmedData>',
    '</PubmedArticle>',
    '<PubmedArticle><MedlineCitation>',
    '<PMID>87654321</PMID>',
    '<Article>',
    '<Journal><JournalIssue><Volume>10</Volume><Issue>1</Issue>',
    '<PubDate><Year>2024</Year></PubDate></JournalIssue>',
    '<Title>Radiology Today</Title><ISOAbbreviation>Radiol Today</ISOAbbreviation></Journal>',
    '<ArticleTitle>Deep learning in radiology.</ArticleTitle>',
    '<Abstract><AbstractText>A concise review of deep learning applications.</AbstractText></Abstract>',
    '<AuthorList>',
    '<Author><LastName>Lee</LastName><ForeName>Kevin</ForeName><Initials>K</Initials>',
    '<AffiliationInfo><Affiliation>Radiology Dept, Stanford, CA, USA.</Affiliation></AffiliationInfo></Author>',
    '</AuthorList>',
    '<PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>',
    '</Article>',
    '<MeshHeadingList><MeshHeading><DescriptorName>Radiology</DescriptorName></MeshHeading></MeshHeadingList>',
    '</MedlineCitation>',
    '<PubmedData><ArticleIdList><ArticleId IdType="doi">10.2000/rt.2024.5</ArticleId></ArticleIdList></PubmedData>',
    '</PubmedArticle>',
    '</PubmedArticleSet>'
  )
}

# === nbib_parse (无网络, 必跑) =================================================

test_that("nbib_parse 解析 .nbib 并产出有效 corpus", {
  tmp <- tempfile(fileext = ".nbib")
  writeLines(.synthetic_nbib(), tmp, useBytes = TRUE)
  on.exit(unlink(tmp))

  M <- suppressWarnings(suppressMessages(nbib_parse(tmp)))
  expect_s3_class(M, "data.frame")
  expect_equal(nrow(M), 2L)
  expect_true(valid_corpus(M))                 # 含 AU/TI/PY
})

test_that("nbib_parse 字段映射正确 (TI/AU/PY/DI/SO/DE/DT/PM/UT)", {
  tmp <- tempfile(fileext = ".nbib")
  writeLines(.synthetic_nbib(), tmp, useBytes = TRUE)
  on.exit(unlink(tmp))
  M <- suppressWarnings(suppressMessages(nbib_parse(tmp)))

  # 第 1 篇 (Smith) — corpus 全大写, 用 grepl 宽松匹配
  expect_true(grepl("MACHINE LEARNING FOR CANCER", M$TI[1]))
  expect_true(grepl("SMITH JA", M$AU[1]))      # AU 大写 姓+名首字母
  expect_true(grepl("DOE J", M$AU[1]))         # 多作者 ";" 分隔
  expect_true(grepl(";", M$AU[1]))
  expect_equal(M$PY[1], 2023)                  # DP → PY 年份
  expect_true(is.numeric(M$PY))
  expect_true(grepl("10.1000/JMI.2023.001", M$DI[1]))  # LID/AID → DI
  expect_true(grepl("MEDICAL INFORMATICS", M$SO[1]))   # JT → SO
  expect_true(grepl("MACHINE LEARNING", M$DE[1]))      # MH → DE
  expect_true(grepl("REVIEW", M$DT[1]))                # PT → DT

  # spec §4.4: PMID → PM + UT
  expect_true(all(c("PM", "UT") %in% names(M)))
  expect_equal(M$PM[1], "12345678")
  expect_equal(M$UT[1], "12345678")
})

test_that("nbib_parse 边界: 路径无效 / 不存在 → NULL", {
  expect_null(suppressWarnings(nbib_parse(NULL)))
  expect_null(suppressWarnings(nbib_parse("")))
  expect_null(suppressWarnings(nbib_parse("/no/such/file.nbib")))
})

# === .parse_efetch_xml (无网络, 必跑) =========================================

test_that(".parse_efetch_xml 解析合成 efetch XML → 有效 corpus", {
  M <- suppressWarnings(suppressMessages(.parse_efetch_xml(.synthetic_efetch_xml())))
  expect_s3_class(M, "data.frame")
  expect_equal(nrow(M), 2L)
  expect_true(valid_corpus(M))

  expect_true(grepl("MACHINE LEARNING FOR CANCER", M$TI[1]))
  expect_true(grepl("SMITH JA", M$AU[1]))
  expect_equal(M$PY[1], 2023)
  expect_true(grepl("10.1000/JMI.2023.001", M$DI[1]))
  expect_true(grepl("REVIEW", M$DT[1]))
  expect_equal(M$PM[1], "12345678")
  expect_equal(M$UT[1], "12345678")
})

test_that(".parse_efetch_xml 边界: 空 / 非法 / 无 PubmedArticle → NULL", {
  expect_null(.parse_efetch_xml(""))
  expect_null(.parse_efetch_xml(NULL))
  expect_null(suppressWarnings(.parse_efetch_xml("<<not xml>>")))
  expect_null(.parse_efetch_xml("<PubmedArticleSet></PubmedArticleSet>"))
})

# === 内部 PMID 判别工具 (无网络) ==============================================

test_that(".pubmed_as_pmids 正确区分 PMID 列表与检索式", {
  expect_equal(.pubmed_as_pmids("12345678"), "12345678")
  expect_equal(.pubmed_as_pmids(c("111", "222")), c("111", "222"))
  expect_equal(.pubmed_as_pmids("111, 222\n333"), c("111", "222", "333"))
  expect_equal(.pubmed_as_pmids(c("111", "111", "222")), c("111", "222"))  # 去重
  expect_null(.pubmed_as_pmids('"machine learning"[Title] AND 2024[PDAT]'))
  expect_null(.pubmed_as_pmids(""))
  expect_null(.pubmed_as_pmids(NULL))
})

# === pubmed_to_corpus 边界 (无网络) ===========================================

test_that("pubmed_to_corpus 空输入 → NULL (不触网)", {
  expect_null(suppressWarnings(pubmed_to_corpus(NULL)))
  expect_null(suppressWarnings(pubmed_to_corpus("")))
  expect_null(suppressWarnings(pubmed_to_corpus(c("", "  "))))
})

# === pubmed_to_corpus 真实网络 (默认 skip) ====================================

test_that("pubmed_to_corpus 真调 NCBI efetch (PMID 入口)", {
  testthat::skip_on_cran()
  testthat::skip_if_offline("eutils.ncbi.nlm.nih.gov")
  skip_if_not(Sys.getenv("RUN_LIVE_LLM") == "true", "需 RUN_LIVE_LLM=true")

  M <- suppressWarnings(suppressMessages(
    pubmed_to_corpus(c("33024307", "32015508"), max_records = 5L)))
  expect_true(valid_corpus(M))
  expect_true(nrow(M) >= 1L)
})

test_that("pubmed_to_corpus 真调 NCBI 检索式入口", {
  testthat::skip_on_cran()
  testthat::skip_if_offline("eutils.ncbi.nlm.nih.gov")
  skip_if_not(Sys.getenv("RUN_LIVE_LLM") == "true", "需 RUN_LIVE_LLM=true")

  M <- suppressWarnings(suppressMessages(
    pubmed_to_corpus('"machine learning"[Title] AND 2024[PDAT]', max_records = 10L)))
  expect_true(valid_corpus(M))
})
