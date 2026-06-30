# 共享测试夹具：被 testthat::test_dir 自动加载
library(bibliometrix)

# 源码相对路径以 tests/testthat 为工作目录
source(file.path("..", "..", "R", "fct_analysis.R"))
source(file.path("..", "..", "R", "ui_helpers.R"))

# 完整语料夹具：bibliometrix 自带的已转换语料
# 注意：bibliometrix >= 5.x 已将内置数据集移至独立的 bibliometrixData 包
data(scientometrics, package = "bibliometrixData")
test_corpus <- function() scientometrics

# 原始 WoS 文本夹具（用于上传解析测试）。公开仓库不提交测试数据文件，测试运行时生成临时文件。
sample_txt_path <- function() {
  path <- tempfile(fileext = ".txt")
  writeLines(c(
    "FN Clarivate Analytics Web of Science",
    "VR 1.0",
    "PT J",
    "AU Smith, J",
    "   Doe, A",
    "AF Smith, John",
    "   Doe, Alice",
    "TI Bibliometric methods for science mapping",
    "SO JOURNAL OF SCIENCE MAPPING",
    "LA English",
    "DT Article",
    "DE bibliometrics; science mapping; co-citation",
    "AB This paper reviews bibliometric methods for science mapping analysis.",
    "C1 [Smith, John] Test University, Department of Science, Testville, USA.",
    "CR Brown D, 2010, J SCIENTOMETR, V1, P1",
    "   Green E, 2012, J RES POLICY, V2, P5",
    "NR 2",
    "TC 25",
    "PY 2019",
    "JI J. Sci. Mapp.",
    "UT WOS:000000000000001",
    "ER",
    "",
    "PT J",
    "AU Lee, K",
    "AF Lee, Kevin",
    "TI Author collaboration networks in scientometrics",
    "SO SCIENTOMETRICS REVIEW",
    "LA English",
    "DT Article",
    "DE collaboration network; co-authorship; scientometrics",
    "AB An analysis of author collaboration networks.",
    "C1 [Lee, Kevin] Metro College, School of Data, Metro City, Australia.",
    "CR Smith J, 2019, J SCI MAPPING, V1, P10",
    "NR 1",
    "TC 8",
    "PY 2022",
    "JI Scientometr. Rev.",
    "UT WOS:000000000000002",
    "ER",
    "",
    "EF"
  ), path, useBytes = TRUE)
  path
}

source(file.path("..", "..", "R", "fct_context.R"))
