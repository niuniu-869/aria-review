# A5 高级图② DTO 单测: 主题战略图 / 主题演进 / 历史引文 / 三字段 Sankey。
# 覆盖各 DTO 成功形状 + 缺字段降级 + 周期不足/节点不足的 not_enough_data。
#
# 样本选择:
#   - thematic / threefield: bibliometrixData::scientometrics 即可点亮 (含 DE/AU/SO)。
#   - evolution / histcite: scientometrics 的 DE 聚类/本地引用太稀疏, thematicEvolution
#     报"空网络"、histNetwork 仅 4 篇有 LCS → 在该数据集上诚实判 not_enough_data/error
#     (非 bug)。这两个图的"成功形状"用真实 WoS IPO 语料 (74 篇, 含丰富 CR/DE) 验证,
#     文件不存在则 skip (CI 无该 fixture 时不阻塞)。

# 真实 IPO 语料 (74 篇 WoS, 含 DE/CR/PY/AU/SO 全字段); 用于 evolution/histcite 成功形状。
.ipo_corpus_path <- "/root/.bibliocn/corpora/2ad152db-e56a-41ea-9287-a5ba40eba057.rds"
.load_ipo <- function() {
  testthat::skip_if_not(file.exists(.ipo_corpus_path), "真实 IPO 语料不可用 (CI 环境)")
  readRDS(.ipo_corpus_path)
}

# ---------- 1) 主题战略图 thematic_dto ----------

test_that("thematic_dto 成功 → clusters[{label,centrality,density,freq}]", {
  data(scientometrics, package = "bibliometrixData")
  e <- thematic_dto(scientometrics, n = 250L, minfreq = 3L)
  expect_true(e$available)
  cl <- e$data$clusters
  expect_true(length(cl) >= 1)
  c1 <- cl[[1]]
  expect_true(all(c("label", "centrality", "density", "freq") %in% names(c1)))
  expect_true(is.numeric(c1$centrality))
  expect_true(is.numeric(c1$density))
  expect_true(jsonlite::validate(jsonlite::toJSON(e, auto_unbox = TRUE, null = "null")))
})

test_that("thematic_dto 缺 DE → missing_field", {
  df <- data.frame(AU = "ARIA M", PY = 2020L, TI = "x", stringsAsFactors = FALSE)
  e <- thematic_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "DE")
})

# ---------- 2) 主题演进 evolution_dto ----------

test_that("evolution_dto 成功 → nodes[{name,period}] + links[{source,target,value}]", {
  M <- .load_ipo()
  e <- evolution_dto(M)
  expect_true(e$available)
  nodes <- e$data$nodes
  links <- e$data$links
  expect_true(length(nodes) >= 2)
  n1 <- nodes[[1]]
  expect_true(all(c("name", "period", "id") %in% names(n1)))
  # 跨周期: period 取值应 >= 2 个 (切出 2-3 周期)
  periods <- unique(vapply(nodes, function(n) n$period, character(1)))
  expect_true(length(periods) >= 2)
  if (length(links)) {
    l1 <- links[[1]]
    expect_true(all(c("source", "target", "value") %in% names(l1)))
  }
  expect_true(jsonlite::validate(jsonlite::toJSON(e, auto_unbox = TRUE, null = "null")))
})

test_that("evolution_dto 缺 DE → missing_field", {
  df <- data.frame(PY = 2020L, AU = "X", TI = "x", stringsAsFactors = FALSE)
  e <- evolution_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "DE")
})

test_that("evolution_dto 周期不足 (单一年份) → not_enough_data", {
  df <- data.frame(DE = c("A;B", "B;C", "A;C"), PY = c(2020L, 2020L, 2020L),
                   AU = c("X", "Y", "Z"), TI = c("a", "b", "c"),
                   stringsAsFactors = FALSE)
  e <- evolution_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "not_enough_data")
})

# ---------- 3) 历史引文 histcite_dto ----------

test_that("histcite_dto 成功 → nodes[{id,year,label,localCites}] + edges[{from,to}]", {
  M <- .load_ipo()
  e <- histcite_dto(M, top = 30L)
  expect_true(e$available)
  nodes <- e$data$nodes
  expect_true(length(nodes) >= 2)
  n1 <- nodes[[1]]
  expect_true(all(c("id", "year", "label", "localCites") %in% names(n1)))
  expect_true(is.character(n1$id))
  # edges 各项含 from/to (可能为空, 但若有则形状对)
  if (length(e$data$edges)) {
    ed <- e$data$edges[[1]]
    expect_true(all(c("from", "to") %in% names(ed)))
  }
  expect_true(jsonlite::validate(jsonlite::toJSON(e, auto_unbox = TRUE, null = "null")))
})

test_that("histcite_dto 缺 CR → missing_field", {
  df <- data.frame(AU = "X", PY = 2020L, TI = "x", stringsAsFactors = FALSE)
  e <- histcite_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "CR")
})

# ---------- 4) 三字段 Sankey threefield_dto ----------

test_that("threefield_dto 成功 → nodes[{name,layer}] + links[{source,target,value}]", {
  data(scientometrics, package = "bibliometrixData")
  e <- threefield_dto(scientometrics, k_au = 10L, k_de = 15L, k_so = 10L)
  expect_true(e$available)
  nodes <- e$data$nodes
  links <- e$data$links
  expect_true(length(nodes) >= 2)
  n1 <- nodes[[1]]
  expect_true(all(c("name", "layer") %in% names(n1)))
  # 三层 layer 应覆盖 0/1/2
  layers <- sort(unique(vapply(nodes, function(n) as.integer(n$layer), integer(1))))
  expect_true(all(c(0L, 1L, 2L) %in% layers))
  expect_true(length(links) >= 1)
  l1 <- links[[1]]
  expect_true(all(c("source", "target", "value") %in% names(l1)))
  expect_true(jsonlite::validate(jsonlite::toJSON(e, auto_unbox = TRUE, null = "null")))
})

test_that("threefield_dto 缺 SO → missing_field", {
  df <- data.frame(AU = "X;Y", DE = "A;B", PY = 2020L, TI = "x", stringsAsFactors = FALSE)
  e <- threefield_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "SO")
})

test_that("threefield_dto 全空 SO 列 (PDF 语料) → missing_field", {
  df <- data.frame(AU = "X;Y", DE = "A;B", SO = c(NA, "", "  "),
                   PY = 2020L, TI = "x", stringsAsFactors = FALSE)
  e <- threefield_dto(df)
  expect_false(e$available)
  expect_equal(e$reason, "missing_field")
  expect_equal(e$missingField, "SO")
})

# ---------- 5) 网络 limit 透传 (A5 §4.4) ----------

test_that("conceptual_dto 接受 n 参数 (limit 透传, 节点数受限)", {
  data(scientometrics, package = "bibliometrixData")
  d5 <- conceptual_dto(scientometrics, n = 5L)
  expect_true(length(d5$graph$nodes) <= 5)
  d100 <- conceptual_dto(scientometrics, n = 100L)
  expect_true(length(d100$graph$nodes) >= length(d5$graph$nodes))
})
