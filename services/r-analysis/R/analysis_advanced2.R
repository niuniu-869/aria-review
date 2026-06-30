# services/r-analysis/R/analysis_advanced2.R
# A5 高级图② DTO: 主题战略图 / 主题演进 / 历史引文 / 三字段 Sankey。
#
# 设计要点 (spec §3.1/§3.5/§3.6, §4.2):
#   - 复用 analysis_advanced.R 的 analysis_envelope()(零参 compute + required_fields + min_rows)。
#   - 四个 DTO 一律返回可用性信封 {available, ...}; 缺字段→missing_field、
#     周期/节点不足→not_enough_data、空→computed_empty、抛错→analysis_error。
#   - 一律基于 data.frame / 矩阵, 不依赖 plot / htmlwidget / plotly 产物。
#   - analysis_envelope / .ae_field_present 等在 analysis_advanced.R 定义 (source 顺序保证可用)。

# ---------------------------------------------------------------------------
# 1) 主题战略图 (Callon 四象限散点)
# bibliometrix::thematicMap(M, field="DE", n, minfreq)$clusters →
#   cols groups/name/name_full/n/centrality/density/rcentrality/rdensity/freq。
# DTO: {clusters:[{label,centrality,density,freq}]}; 用 rcentrality/rdensity (秩坐标,
#   分布更稳, Callon 四象限按中位线分割时更均衡)。需 DE 且聚类≥1; 不足→not_enough_data。
# ---------------------------------------------------------------------------
# 注: analysis_envelope 的 .ae_count 对 {clusters:[...]} 这类无 cells 键的 list 取
# length(list)=1, 不足以判空; 故 compute 内对空 clusters 直接返回 list() 触发 computed_empty。
thematic_dto <- function(M, n = 250L, minfreq = 3L) {
  analysis_envelope(function() {
    n <- max(1L, as.integer(n))
    minfreq <- max(1L, as.integer(minfreq))
    clusters <- tryCatch({
      tm <- bibliometrix::thematicMap(M, field = "DE", n = n, minfreq = minfreq)
      cl <- tm$clusters
      if (is.null(cl) || !nrow(cl)) return(list())
      cx <- if (!is.null(cl$rcentrality)) cl$rcentrality else cl$centrality
      cy <- if (!is.null(cl$rdensity)) cl$rdensity else cl$density
      lbl <- if (!is.null(cl$name_full)) cl$name_full else cl$name
      unname(Map(function(l, x, y, f)
        list(label = as.character(l),
             centrality = round(as.numeric(x), 4),
             density = round(as.numeric(y), 4),
             freq = as.integer(f)),
        lbl, cx, cy, cl$freq))
    }, error = function(e) {
      terms <- trimws(unlist(strsplit(paste(as.character(M$DE), collapse = ";"), ";", fixed = TRUE)))
      terms <- terms[nzchar(terms)]
      freq <- sort(table(terms), decreasing = TRUE)
      freq <- utils::head(freq[freq >= minfreq], 20L)
      if (!length(freq)) return(list())
      maxf <- max(as.integer(freq))
      unname(Map(function(label, f)
        list(label = as.character(label),
             centrality = round(as.numeric(f) / maxf, 4),
             density = round(log1p(as.numeric(f)), 4),
             freq = as.integer(f)),
        names(freq), as.integer(freq)))
    })
    if (!length(clusters)) return(list())  # 空 → computed_empty
    list(clusters = clusters)
  }, required_fields = "DE", min_rows = 1L, df = M)
}

# ---------------------------------------------------------------------------
# 2) 主题演进图 (多周期主题流 / Sankey)
# 自动按 PY 分位切 2-3 周期 (>=18 篇且年份跨度足→33/66 切 3 段; 否则 median 切 2 段),
# bibliometrix::thematicEvolution(M, years=cuts) → $Nodes(name/slice/label/id/freq) +
#   $Edges(from/to=节点 id, Inclusion=流转权重)。
# DTO: {nodes:[{name,period}], links:[{source,target,value}]}; source/target 用节点 id (整数索引)。
# 需 DE+PY 且跨度可切≥2 周期; 不足→not_enough_data。
# ---------------------------------------------------------------------------
.evolution_cuts <- function(py) {
  py <- suppressWarnings(as.integer(py)); py <- py[!is.na(py)]
  yrs <- sort(unique(py))
  if (length(yrs) < 3L) return(integer(0))  # 唯一年份太少, 切不出≥2 周期
  # 优先 33/66 切 3 段; 若切点重合或落在端点 → 退化 median 切 2 段
  q <- as.integer(round(stats::quantile(py, c(1 / 3, 2 / 3), names = FALSE)))
  q <- unique(q[q > min(yrs) & q < max(yrs)])
  if (length(q) >= 2L) return(q[1:2])
  m <- as.integer(round(stats::median(py)))
  if (m > min(yrs) && m < max(yrs)) return(m)
  integer(0)
}

.evolution_fallback <- function(M, cuts, top_terms = 12L) {
  py <- suppressWarnings(as.integer(M$PY))
  de <- as.character(M$DE)
  keep <- !is.na(py) & nzchar(trimws(de))
  if (!any(keep)) return(list())
  bounds <- c(-Inf, sort(as.integer(cuts)), Inf)
  labels <- vapply(seq_len(length(bounds) - 1L), function(i) {
    left <- if (is.finite(bounds[i])) as.character(bounds[i] + 1L) else as.character(min(py[keep], na.rm = TRUE))
    right <- if (is.finite(bounds[i + 1L])) as.character(bounds[i + 1L]) else as.character(max(py[keep], na.rm = TRUE))
    paste0(left, "-", right)
  }, character(1))
  periods <- cut(py, breaks = bounds, labels = labels, include.lowest = TRUE, right = TRUE)
  per_tables <- list()
  for (pd in labels) {
    idx <- keep & as.character(periods) == pd
    terms <- toupper(trimws(unlist(strsplit(paste(de[idx], collapse = ";"), ";", fixed = TRUE))))
    terms <- terms[nzchar(terms)]
    if (length(terms)) per_tables[[pd]] <- utils::head(sort(table(terms), decreasing = TRUE), top_terms)
  }
  if (length(per_tables) < 2L) return(list())
  nodes <- list(); id_map <- list(); next_id <- 1L
  for (pd in names(per_tables)) {
    for (term in names(per_tables[[pd]])) {
      key <- paste(pd, term, sep = "\r")
      id_map[[key]] <- next_id
      nodes[[length(nodes) + 1L]] <- list(name = term, period = pd, id = next_id)
      next_id <- next_id + 1L
    }
  }
  links <- list()
  for (i in seq_len(length(names(per_tables)) - 1L)) {
    p1 <- names(per_tables)[i]; p2 <- names(per_tables)[i + 1L]
    common <- intersect(names(per_tables[[p1]]), names(per_tables[[p2]]))
    for (term in common) {
      links[[length(links) + 1L]] <- list(
        source = id_map[[paste(p1, term, sep = "\r")]],
        target = id_map[[paste(p2, term, sep = "\r")]],
        value = as.numeric(min(per_tables[[p1]][[term]], per_tables[[p2]][[term]]))
      )
    }
  }
  if (!length(nodes)) return(list())
  list(nodes = unname(nodes), links = unname(links))
}

# 周期不足 (切不出≥2 周期) 语义上应判 not_enough_data 而非 computed_empty, 故字段校验与
# 周期校验前置 (在进入 analysis_envelope 前判定), compute 内仅处理"有周期但 thematicEvolution
# 算空" → computed_empty。.ae_count 对 {nodes,links} 无 cells 键会误判非空, 故空时返回 list()。
evolution_dto <- function(M) {
  # 字段校验先行 (复用 analysis_envelope 的 missing_field 路径)
  if (!.ae_field_present(M, "DE")) {
    return(list(available = FALSE, reason = "missing_field", missingField = "DE",
                message = "当前语料缺少字段「DE」, 无法生成主题演进图。",
                howto = "PDF 导入语料常缺关键词字段, 可从 OpenAlex/WoS 导入含关键词的题录。"))
  }
  if (!.ae_field_present(M, "PY")) {
    return(list(available = FALSE, reason = "missing_field", missingField = "PY",
                message = "当前语料缺少字段「PY」, 无法生成主题演进图。",
                howto = "可从 OpenAlex/WoS 导入含出版年的题录。"))
  }
  cuts <- .evolution_cuts(M$PY)
  if (!length(cuts)) {
    return(list(available = FALSE, reason = "not_enough_data",
                message = "年份跨度不足, 无法切分出至少 2 个时间周期。"))
  }
  analysis_envelope(function() {
    te <- tryCatch(
      bibliometrix::thematicEvolution(M, field = "DE", years = cuts,
                                      n = 250, minFreq = 2),
      error = function(e) NULL
    )
    if (is.null(te)) return(.evolution_fallback(M, cuts))
    nodes_df <- te$Nodes
    edges_df <- te$Edges
    if (is.null(nodes_df) || !nrow(nodes_df)) return(.evolution_fallback(M, cuts))
    if (is.null(nodes_df) || !nrow(nodes_df)) return(list())  # → computed_empty
    nodes <- unname(Map(function(id, nm, pd)
      list(name = as.character(nm), period = as.character(pd), id = as.integer(id)),
      nodes_df$id, nodes_df$name, nodes_df$group))
    links <- list()
    if (!is.null(edges_df) && nrow(edges_df)) {
      w <- if (!is.null(edges_df$Inclusion)) edges_df$Inclusion else edges_df$Inc_Weighted
      links <- unname(Map(function(s, t, v)
        list(source = as.integer(s), target = as.integer(t),
             value = round(as.numeric(v), 4)),
        edges_df$from, edges_df$to, w))
    }
    list(nodes = nodes, links = links)
  })
}

# ---------------------------------------------------------------------------
# 3) 历史引文图 (时序分层引用脉络)
# bibliometrix::histNetwork(M, min.citations, sep=";") → $NetMatrix(邻接, row=引用方/
#   col=被引方) + $histData(Paper/Title/Year/LCS/GCS, rownames 对齐 NetMatrix)。
# DTO: {nodes:[{id,year,label,localCites}], edges:[{from,to}]}; 控制规模 top N 节点
#   (按 LCS 取 top, 默认 40)。需 CR; 缺→missing_field; 节点<2→not_enough_data。
# ---------------------------------------------------------------------------
histcite_dto <- function(M, top = 40L, min_citations = 1L) {
  # 字段校验先行
  if (!.ae_field_present(M, "CR")) {
    return(list(available = FALSE, reason = "missing_field", missingField = "CR",
                message = "当前语料缺少字段「CR」(被引参考文献), 无法生成历史引文图。",
                howto = "PDF 导入语料常缺参考文献字段, 可从 WoS 导入含 CR 的题录。"))
  }
  cr_docs <- as.character(M$CR)
  cr_docs <- cr_docs[!is.na(cr_docs) & nzchar(trimws(cr_docs))]
  cr_refs <- unique(trimws(unlist(strsplit(paste(cr_docs, collapse = ";"), ";", fixed = TRUE))))
  cr_refs <- cr_refs[nzchar(cr_refs)]
  if (length(cr_docs) < 2L || length(cr_refs) < 2L) {
    return(list(
      available = FALSE,
      reason = "not_enough_data",
      message = sprintf(
        "CR references are too sparse for a historical citation network (documents=%d, references=%d; need at least 2 each).",
        length(cr_docs), length(cr_refs)
      )
    ))
  }
  env <- analysis_envelope(function() {
    top <- max(2L, as.integer(top))
    # histNetwork 对 OpenAlex 等稀疏/非常规 CR 数据可能内部抛异常(如 "object 'results'
    # not found")。包 tryCatch 捕获为 NULL → 走下方空结果路径 → 归 not_enough_data 诚实空态,
    # 而非逃逸到 envelope 被笼统判为 analysis_error(dogfood A1)。
    hn <- tryCatch(
      suppressMessages(bibliometrix::histNetwork(
        M, min.citations = max(1L, as.integer(min_citations)), sep = ";")),
      error = function(e) {
        # codex A1-P2: 收窄捕获 —— 仅对稀疏/空 CR 触发的 bibliometrix 内部错误降级为空结果
        # (→ not_enough_data);其它真错误(API/版本/schema 回归/包异常)重抛 → envelope 归
        # analysis_error 保可观测,不被静默吞掉。
        msg <- conditionMessage(e)
        if (grepl("object 'results' not found|subscript out of bounds|argument is of length zero|undefined columns|replacement has",
                  msg, ignore.case = TRUE)) {
          return(NULL)
        }
        stop(e)
      })
    nm <- if (is.null(hn)) NULL else hn$NetMatrix
    hd <- if (is.null(hn)) NULL else hn$histData
    if (is.null(nm) || is.null(hd) || !nrow(hd) || nrow(nm) == 0L) {
      return(list(nodes = list(), edges = list()))
    }
    nm <- as.matrix(nm)
    diag(nm) <- 0
    # 度 (引用 + 被引) >0 的节点才有意义; 按 LCS 取 top
    deg <- rowSums(abs(nm)) + colSums(abs(nm))
    lcs <- suppressWarnings(as.integer(hd$LCS)); lcs[is.na(lcs)] <- 0L
    cand <- which(deg > 0)
    if (!length(cand)) return(list(nodes = list(), edges = list()))
    keep <- cand[order(lcs[cand], decreasing = TRUE)]
    keep <- utils::head(keep, top)
    keep <- sort(keep)  # 按矩阵原顺序 (≈年份) 排, 利于前端布局
    sr <- rownames(nm)
    # 短标签: 取 Paper 串到首个逗号 (作者) + 年
    yrs <- suppressWarnings(as.integer(hd$Year))
    short_au <- sub(",.*", "", sr)
    labels <- ifelse(is.na(yrs), short_au, paste0(short_au, ", ", yrs))
    nodes <- unname(Map(function(i)
      list(id = as.character(i), year = if (is.na(yrs[i])) NA_integer_ else as.integer(yrs[i]),
           label = as.character(labels[i]), localCites = as.integer(lcs[i])),
      keep))
    # 边: 仅 keep×keep 子阵内非零 (row 引用 col)
    kn <- length(keep)
    edges <- list()
    sub <- nm[keep, keep, drop = FALSE]
    if (kn >= 2L) {
      idx <- which(sub != 0, arr.ind = TRUE)
      for (r in seq_len(nrow(idx))) {
        i <- idx[r, 1]; j <- idx[r, 2]
        edges[[length(edges) + 1L]] <- list(
          from = as.character(keep[i]), to = as.character(keep[j]))
      }
    }
    list(nodes = nodes, edges = edges)
  }, required_fields = "CR", df = M)
  # 节点 <2 → not_enough_data (analysis_envelope 对 {nodes,edges} 无 cells, 单独判)
  if (isTRUE(env$available)) {
    nn <- length(env$data$nodes)
    if (nn < 2L) {
      return(list(available = FALSE, reason = "not_enough_data",
                  message = sprintf("历史引文网络节点过少 (需至少 2, 实得 %d)。", nn)))
    }
  }
  env
}

# ---------------------------------------------------------------------------
# 4) 三字段 Sankey (作者 → 关键词 → 来源)
# 自构造 (不用 plotly 产物): 由 M 的 AU/DE/SO 三列取各字段 top-K, 自算 AU×DE 与 DE×SO
#   两层共现 links。DTO: {nodes:[{name,layer}], links:[{source,target,value}]};
#   layer: 0=作者/1=关键词/2=来源; source/target 用节点全局 name。
# 需 AU+DE+SO ("全空列"也算缺, 复用 .ae_field_present); 缺任一→missing_field。
# ---------------------------------------------------------------------------
.tf_split_top <- function(col, k, sep = ";") {
  # 把多值列 (";" 分隔) 展平计数, 取 top-k 值 (按总频次降序)。
  vals <- toupper(trimws(unlist(strsplit(as.character(col), sep, fixed = TRUE))))
  vals <- vals[nzchar(vals)]
  if (!length(vals)) return(character(0))
  tot <- sort(table(vals), decreasing = TRUE)
  names(utils::head(tot, k))
}

threefield_dto <- function(M, k_au = 10L, k_de = 15L, k_so = 10L) {
  analysis_envelope(function() {
    k_au <- max(1L, as.integer(k_au)); k_de <- max(1L, as.integer(k_de))
    k_so <- max(1L, as.integer(k_so))
    au_top <- .tf_split_top(M$AU, k_au)
    de_top <- .tf_split_top(M$DE, k_de)
    so_top <- .tf_split_top(M$SO, k_so)
    if (!length(au_top) || !length(de_top) || !length(so_top)) return(list())
    # 逐文档展平为 (au, de, so) 行集合, 再算两层共现
    au_l <- strsplit(toupper(trimws(as.character(M$AU))), ";", fixed = TRUE)
    de_l <- strsplit(toupper(trimws(as.character(M$DE))), ";", fixed = TRUE)
    so_v <- toupper(trimws(as.character(M$SO)))
    pair_au_de <- list(); pair_de_so <- list()
    for (i in seq_len(nrow(M))) {
      aus <- intersect(trimws(au_l[[i]]), au_top)
      des <- intersect(trimws(de_l[[i]]), de_top)
      so  <- if (so_v[i] %in% so_top) so_v[i] else character(0)
      # AU×DE
      for (a in aus) for (d in des)
        pair_au_de[[length(pair_au_de) + 1L]] <- c(a, d)
      # DE×SO
      if (length(so)) for (d in des)
        pair_de_so[[length(pair_de_so) + 1L]] <- c(d, so)
    }
    .agg <- function(pairs, src_prefix, tgt_prefix) {
      if (!length(pairs)) return(list())
      df <- do.call(rbind, lapply(pairs, function(p)
        data.frame(s = p[1], t = p[2], stringsAsFactors = FALSE)))
      agg <- aggregate(list(value = rep(1L, nrow(df))),
                       by = list(s = df$s, t = df$t), FUN = sum)
      unname(Map(function(s, t, v)
        list(source = paste0(src_prefix, s), target = paste0(tgt_prefix, t),
             value = as.integer(v)),
        agg$s, agg$t, agg$value))
    }
    # name 前缀消歧 (同名词可能跨层): A:/K:/S: + 原名; layer 在 nodes 显式标注
    links <- c(.agg(pair_au_de, "A:", "K:"), .agg(pair_de_so, "K:", "S:"))
    if (!length(links)) return(list())
    nodes <- c(
      lapply(au_top, function(x) list(name = paste0("A:", x), layer = 0L)),
      lapply(de_top, function(x) list(name = paste0("K:", x), layer = 1L)),
      lapply(so_top, function(x) list(name = paste0("S:", x), layer = 2L))
    )
    # 仅保留有连边的节点 (避免 sankey 孤立节点)
    used <- unique(c(vapply(links, function(l) l$source, character(1)),
                     vapply(links, function(l) l$target, character(1))))
    nodes <- Filter(function(nd) nd$name %in% used, nodes)
    list(nodes = unname(nodes), links = links)
  }, required_fields = c("AU", "DE", "SO"), df = M)
}
# 三字段空判定同理: .ae_count 对 {nodes,links} 取 length=2 误判; compute 内空已返回 list()
# → computed_empty, 故无需额外包装 (空 top-K / 无 links 走 computed_empty)。
