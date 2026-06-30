# fct_context.R — 上下文构建器：分析层与 LLM 层之间的唯一接口
# 把分散的分析产出汇聚成 LLM 可消费的结构化对象（阶段二 LLM 模块的输入）

#' 构建 LLM 上下文
#' @param M 语料 data.frame
#' @param top_n 高被引文献取前 N 篇
#' @return list(corpus_summary, theme_clusters, top_docs, trend_topics)
build_context <- function(M, top_n = 20) {
  years <- suppressWarnings(as.integer(M$PY))
  years <- years[!is.na(years)]
  corpus_summary <- list(
    n_docs = nrow(M),
    year_range = if (length(years)) range(years) else c(NA, NA),
    n_sources = length(unique(M$SO))
  )

  concept <- analyze_conceptual(M)
  theme_clusters <- concept$thematic_map$clusters

  docs <- analyze_documents(M)
  top_docs_full <- docs$most_cited_docs
  ab <- if ("AB" %in% names(M)) M$AB else rep("", nrow(M))
  top_docs_full$abstract <- ab[order(suppressWarnings(as.numeric(M$TC)),
                                     decreasing = TRUE)]
  top_docs <- utils::head(top_docs_full, top_n)

  list(
    corpus_summary = corpus_summary,
    theme_clusters = theme_clusters,
    top_docs = top_docs,
    trend_topics = docs$trend_topics
  )
}
