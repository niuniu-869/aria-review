# services/r-analysis/R/analysis_networks.R
# 切片4 网络页 DTO: conceptual(关键词共现) / intellectual(共被引) / social(合作网络)
# Codex step1-#6: 网络返回 nodes/edges DTO, 不返回 igraph/plotly 对象。
# 从 bibliometrix biblioNetwork 邻接矩阵取 top-N 节点(按强度)+ 其间边, 版本无关。

#' 邻接矩阵 → {nodes, edges} (top-N 按强度)
#' 注: 不全 densify (大语料 co-citation 矩阵可达数千阶); 在稀疏阵上算强度,
#' 仅对 top-N×top-N 子阵 densify (Codex 大对象防护)。
.net_dto <- function(net_mat, n = 30L) {
  if (is.null(net_mat) || is.null(rownames(net_mat)) || nrow(net_mat) == 0L) {
    return(list(nodes = list(), edges = list()))
  }
  diag(net_mat) <- 0  # Matrix/base 均支持; 去自环
  strength <- as.numeric(Matrix::rowSums(abs(net_mat), na.rm = TRUE))
  keep <- utils::head(order(strength, decreasing = TRUE), max(1L, as.integer(n)))
  keep <- keep[is.finite(strength[keep]) & strength[keep] > 0]  # 防 NA/非有限 (Codex slice4-P2)
  if (!length(keep)) return(list(nodes = list(), edges = list()))
  labels <- rownames(net_mat)[keep]
  nodes <- unname(Map(
    function(lbl, s) list(id = as.character(lbl), label = as.character(lbl),
                          value = round(as.numeric(s), 3)),
    labels, strength[keep]))
  sub <- as.matrix(net_mat[keep, keep, drop = FALSE])  # 仅 NxN densify
  edges <- list()
  kn <- length(keep)
  if (kn >= 2L) {
    for (i in seq_len(kn - 1L)) {
      for (j in seq.int(i + 1L, kn)) {
        w <- sub[i, j]
        if (!is.na(w) && is.finite(w) && w > 0) {
          edges[[length(edges) + 1L]] <- list(
            source = as.character(labels[i]),
            target = as.character(labels[j]),
            weight = round(as.numeric(w), 3))
        }
      }
    }
  }
  list(nodes = nodes, edges = edges)
}

#' 从 DE 列自建关键词共现矩阵(bibliometrix 路径返回空时的鲁棒兜底)
.keyword_net_from_de <- function(M) {
  if (!"DE" %in% names(M)) return(NULL)
  docs <- as.character(M$DE)
  docs <- docs[!is.na(docs) & nzchar(trimws(docs))]
  if (!length(docs)) return(NULL)

  term_docs <- lapply(docs, function(x) {
    terms <- trimws(unlist(strsplit(x, ";", fixed = TRUE)))
    unique(terms[nzchar(terms)])
  })
  terms <- sort(unique(unlist(term_docs)))
  if (length(terms) < 2L) return(NULL)

  mat <- matrix(0, nrow = length(terms), ncol = length(terms),
                dimnames = list(terms, terms))
  for (doc_terms in term_docs) {
    if (length(doc_terms) == 1L) {
      mat[doc_terms, doc_terms] <- mat[doc_terms, doc_terms] + 1
    } else if (length(doc_terms) > 1L) {
      pairs <- utils::combn(doc_terms, 2L)
      for (i in seq_len(ncol(pairs))) {
        a <- pairs[1L, i]; b <- pairs[2L, i]
        mat[a, b] <- mat[a, b] + 1
        mat[b, a] <- mat[b, a] + 1
      }
    }
  }
  mat
}

#' 概念结构: 关键词共现网络
#' bibliometrix 中 network="keywords" 取 ID(Keywords Plus, WoS 专有), network=
#' "author_keywords" 取 DE(作者关键词)。BiblioCN 题录的关键词统一落在 DE, 缺 ID 列时
#' 用 "keywords" 会触发 "undefined columns selected"。故 ID 非空时用之, 否则回退 DE。
conceptual_dto <- function(M, n = 30L) {
  if (!is.data.frame(M) || nrow(M) == 0L) stop("conceptual_dto: 空语料")
  has_id <- "ID" %in% names(M) && any(nzchar(trimws(as.character(M$ID))), na.rm = TRUE)
  net <- if (has_id) "keywords" else "author_keywords"
  g <- tryCatch(
    .net_dto(bibliometrix::biblioNetwork(M, analysis = "co-occurrences",
                                         network = net, sep = ";"), n),
    error = function(e) list(nodes = list(), edges = list()))
  # 双保险(并入 competition): bibliometrix 路径返回空时,回退自建 DE 共现矩阵
  if (!length(g$nodes)) {
    g <- .net_dto(.keyword_net_from_de(M), n)
  }
  list(schemaVersion = 1L, network = "co-occurrence-keywords", graph = g)
}

#' 知识结构: 参考文献共被引网络
intellectual_dto <- function(M, n = 30L) {
  if (!is.data.frame(M) || nrow(M) == 0L) stop("intellectual_dto: 空语料")
  g <- tryCatch(
    .net_dto(bibliometrix::biblioNetwork(M, analysis = "co-citation",
                                         network = "references", sep = ";"), n),
    error = function(e) list(nodes = list(), edges = list()))
  list(schemaVersion = 1L, network = "co-citation-references", graph = g)
}

#' 网络端点 limit 钳制 (A5/§4.4): 钳到 [1, 100], 默认 100 (NA/非数 → 100)。
#' 与 plumber 路由解耦放此, 便于 testthat 直接覆盖边界 (limit=0/-1/101)。
.net_limit <- function(limit) {
  n <- suppressWarnings(as.integer(limit))
  if (is.na(n)) return(100L)
  min(100L, max(1L, n))  # 下限 1 / 上限 100 (codex A5 P2: limit<1 应钳到 1, 非 100)
}

#' 社会结构: 作者合作网络 + 国家合作网络
social_dto <- function(M, n = 30L) {
  if (!is.data.frame(M) || nrow(M) == 0L) stop("social_dto: 空语料")
  author <- tryCatch(
    .net_dto(bibliometrix::biblioNetwork(M, analysis = "collaboration",
                                         network = "authors", sep = ";"), n),
    error = function(e) list(nodes = list(), edges = list()))
  country <- tryCatch({
    M2 <- bibliometrix::metaTagExtraction(M, Field = "AU_CO", sep = ";")
    .net_dto(bibliometrix::biblioNetwork(M2, analysis = "collaboration",
                                         network = "countries", sep = ";"), n)
  }, error = function(e) list(nodes = list(), edges = list()))
  list(schemaVersion = 1L, authorCollab = author, countryCollab = country)
}
