# ingest_openalex.R — 主题词/参考文献 → OpenAlex → WoS plaintext 文件
#
# 移植自 legacy fct_openalex_to_corpus.R + fct_llm_parse_refs.R 的 OpenAlex 反查段。
# 战略意义: 把"没有 WoS/Scopus 数据"的新人门槛打掉 (欢迎页路径 A 主题词 / B 参考文献)。
#
# 与 legacy 的差异:
#   - 不再用 on_progress (Shiny 进度条), 改同步请求/响应; 前端用 spinner 反馈。
#   - 不直接 convert2df, 而是产出 WoS plaintext 临时文件路径, 交给 store.R 的
#     parse_and_store() 统一做 convert2df + 状态机 + 落盘 (DRY, 一致性最强)。
#   - LLM 抽题录 (路径 B 第一阶段) 在 agent (Python) 做; 这里只负责 OpenAlex 反查。
#
# OpenAlex polite pool: 所有请求带 mailto (OPENALEX_EMAIL 环境变量, 缺省项目联系人)。
# 依赖: httr2 (legacy renv 库已含; Dockerfile 需补装)。

OA_BASE         <- "https://api.openalex.org"
OA_DEFAULT_MAIL <- "aria-review@users.noreply.github.com"  # 学术礼貌联系人
OA_PAGE_SIZE    <- 200L     # OpenAlex 单页上限
OA_REFS_BATCH   <- 50L      # filter=ids.openalex:W1|W2|... 单次 ID 数
OA_TIMEOUT_S    <- 30

#' 清洗 OpenAlex 文本: 去 HTML 标签 / 实体 / 控制字符 (WoS plaintext 不允许这些)
.oa_clean_text <- function(s) {
  if (is.null(s) || !nzchar(s)) return("")
  s <- gsub("<[^>]+>", " ", s, perl = TRUE)            # HTML 标签
  s <- gsub("&[a-zA-Z]+;", " ", s, perl = TRUE)        # 命名实体
  s <- gsub("&#x?[0-9a-fA-F]+;", " ", s, perl = TRUE)  # 数值实体
  s <- gsub("[[:cntrl:]]", " ", s)                      # 控制字符
  s <- gsub("\\s+", " ", s)                              # 折叠空白
  trimws(s)
}

#' OpenAlex polite pool 用的联系邮箱
.oa_mail <- function() {
  v <- Sys.getenv("OPENALEX_EMAIL", unset = "")
  if (nzchar(v)) v else OA_DEFAULT_MAIL
}

#' 还原 OpenAlex abstract_inverted_index → 原文
#' OpenAlex 出于版权把摘要存成倒排索引 {"word": [pos,...]}; 按 pos 升序重组。
.oa_inverted_to_abstract <- function(inv_idx) {
  if (is.null(inv_idx) || length(inv_idx) == 0) return("")
  words <- character(0)
  positions <- integer(0)
  for (w in names(inv_idx)) {
    pos <- inv_idx[[w]]
    if (!length(pos)) next
    pos_int <- suppressWarnings(as.integer(unlist(pos)))
    pos_int <- pos_int[!is.na(pos_int)]
    if (!length(pos_int)) next
    words     <- c(words, rep(w, length(pos_int)))
    positions <- c(positions, pos_int)
  }
  if (!length(words)) return("")
  paste(words[order(positions)], collapse = " ")
}

#' 标题相似度 (0-1): 归一化后用 adist 编辑距离, 不依赖 stringdist。
.oa_title_sim <- function(a, b) {
  norm <- function(s) gsub("[^a-z0-9]", "", tolower(s %||% ""))
  na <- norm(a); nb <- norm(b)
  if (!nzchar(na) || !nzchar(nb)) return(0)
  d <- utils::adist(na, nb)[1, 1]
  1 - d / max(nchar(na), nchar(nb))
}

#' 按 DOI 取单条 OpenAlex work (404 不报错, 返回 NULL)
.oa_get_work_by_doi <- function(doi) {
  if (is.null(doi) || !nzchar(doi)) return(NULL)
  doi <- sub("^https?://(dx\\.)?doi\\.org/", "", doi, ignore.case = TRUE)
  req <- httr2::request(sprintf("%s/works/doi:%s", OA_BASE, doi)) |>
    httr2::req_url_query(mailto = .oa_mail()) |>
    httr2::req_timeout(OA_TIMEOUT_S) |>
    httr2::req_user_agent(sprintf("BiblioCN/0.1 (mailto:%s)", .oa_mail())) |>
    httr2::req_error(is_error = function(resp) FALSE)
  resp <- tryCatch(httr2::req_perform(req), error = function(e) NULL)
  if (is.null(resp) || httr2::resp_status(resp) >= 400) return(NULL)
  httr2::resp_body_json(resp, simplifyVector = FALSE)
}

#' 按标题搜索 OpenAlex, 返回相似度最高的 work (≥ sim_min 才接受)
.oa_search_by_title <- function(title, sim_min = 0.7) {
  if (is.null(title) || !nzchar(title)) return(NULL)
  req <- httr2::request(paste0(OA_BASE, "/works")) |>
    httr2::req_url_query(
      filter    = sprintf("title.search:%s", title),
      `per-page`= 5L,
      mailto    = .oa_mail()
    ) |>
    httr2::req_timeout(OA_TIMEOUT_S) |>
    httr2::req_user_agent(sprintf("BiblioCN/0.1 (mailto:%s)", .oa_mail())) |>
    httr2::req_error(is_error = function(resp) FALSE)
  resp <- tryCatch(httr2::req_perform(req), error = function(e) NULL)
  if (is.null(resp) || httr2::resp_status(resp) >= 400) return(NULL)
  body <- httr2::resp_body_json(resp, simplifyVector = FALSE)
  results <- body$results %||% list()
  if (!length(results)) return(NULL)
  best <- NULL; best_sim <- 0
  for (w in results) {
    sim <- .oa_title_sim(title, w$title %||% w$display_name %||% "")
    if (sim > best_sim) { best_sim <- sim; best <- w }
  }
  if (best_sim < sim_min) return(NULL)
  list(work = best, sim = best_sim)
}

#' OpenAlex 主题词检索, 按相关度返回近 since 年发表的 n 篇文献 (完整 work 对象)
#' 返回值:
#'   - 正常时: work 对象列表 (0..n 条)
#'   - 网络/HTTP 错误时: list(error=TRUE, status=<int>, message=<chr>)
#'     → 调用方须用 .oa_is_error() 检查, 与"真空(0命中)"区分
.oa_search_works <- function(query, n = 50, since = "2016-01-01") {
  works <- list()
  cursor <- "*"
  page_sz <- min(OA_PAGE_SIZE, max(n, 25L))
  while (length(works) < n && !is.null(cursor) && nzchar(cursor)) {
    req <- httr2::request(paste0(OA_BASE, "/works")) |>
      httr2::req_url_query(
        search    = query,
        filter    = sprintf("from_publication_date:%s,type:article,has_doi:true", since),
        `per-page`= page_sz,
        sort      = "relevance_score:desc",
        cursor    = cursor,
        mailto    = .oa_mail()
      ) |>
      httr2::req_timeout(OA_TIMEOUT_S) |>
      httr2::req_user_agent(sprintf("BiblioCN/0.1 (mailto:%s)", .oa_mail())) |>
      httr2::req_error(is_error = function(resp) FALSE)
    resp <- tryCatch(httr2::req_perform(req), error = function(e) {
      # 网络/连接级错误 → 返回错误信号
      list(.__oa_error__ = TRUE, status = 0L, message = conditionMessage(e))
    })
    # 连接级错误
    if (is.list(resp) && isTRUE(resp$.__oa_error__)) {
      return(list(error = TRUE, status = resp$status,
                  message = paste0("OpenAlex 网络错误: ", resp$message)))
    }
    # HTTP >= 400 错误
    http_status <- httr2::resp_status(resp)
    if (http_status >= 400L) {
      msg <- tryCatch(
        httr2::resp_body_json(resp, simplifyVector = FALSE)$message %||%
          paste0("HTTP ", http_status),
        error = function(e) paste0("HTTP ", http_status)
      )
      return(list(error = TRUE, status = http_status,
                  message = paste0("OpenAlex 返回 ", http_status, ": ", msg)))
    }
    body <- httr2::resp_body_json(resp, simplifyVector = FALSE)
    results <- body$results %||% list()
    if (!length(results)) break
    need <- n - length(works)
    works <- c(works, utils::head(results, need))
    if (length(works) >= n) break
    cursor <- body$meta$next_cursor
  }
  works
}

#' 检查 .oa_search_works 返回值是否是错误信号
.oa_is_error <- function(x) is.list(x) && isTRUE(x$error)

#' 批量取 referenced works 题录 (组装 CR 字段); 去重后分批 /works?filter=ids.openalex
.oa_resolve_refs_batch <- function(ref_ids) {
  if (!length(ref_ids)) return(list())
  ref_ids <- unique(ref_ids)
  short_ids <- sub("^.*/(W[0-9]+)$", "\\1", ref_ids)
  short_ids <- short_ids[grepl("^W[0-9]+$", short_ids)]
  if (!length(short_ids)) return(list())
  out <- list()
  batches <- split(short_ids, ceiling(seq_along(short_ids) / OA_REFS_BATCH))
  for (i in seq_along(batches)) {
    chunk <- batches[[i]]
    req <- httr2::request(paste0(OA_BASE, "/works")) |>
      httr2::req_url_query(
        filter    = paste0("ids.openalex:", paste(chunk, collapse = "|")),
        `per-page`= OA_REFS_BATCH,
        select    = "id,doi,publication_year,authorships,primary_location,biblio",
        mailto    = .oa_mail()
      ) |>
      httr2::req_timeout(OA_TIMEOUT_S) |>
      httr2::req_user_agent(sprintf("BiblioCN/0.1 (mailto:%s)", .oa_mail())) |>
      httr2::req_error(is_error = function(resp) FALSE)
    resp <- tryCatch(httr2::req_perform(req), error = function(e) NULL)
    if (is.null(resp) || httr2::resp_status(resp) >= 400) next
    body <- httr2::resp_body_json(resp, simplifyVector = FALSE)
    for (w in (body$results %||% list())) {
      key <- sub("^.*/", "", w$id %||% "")
      if (!nzchar(key)) next
      jname <- .oa_clean_text(w$primary_location$source$display_name %||% "")
      out[[key]] <- list(
        author_short  = .oa_first_author_short(w$authorships),
        year          = as.integer(w$publication_year %||% NA_integer_),
        journal_short = toupper(substr(jname, 1, 60)),
        doi           = sub("^https?://(dx\\.)?doi\\.org/", "",
                            w$doi %||% "", ignore.case = TRUE),
        volume        = w$biblio$volume %||% "",
        first_page    = w$biblio$first_page %||% ""
      )
    }
  }
  out
}

#' 从 authorships 取第一作者的 "LASTNAME FI" 短形式 (WoS CR 用)
.oa_first_author_short <- function(authorships) {
  if (!length(authorships)) return("ANON")
  a1 <- authorships[[1]]$author$display_name %||% ""
  if (!nzchar(a1)) return("ANON")
  parts <- strsplit(a1, "\\s+")[[1]]
  if (length(parts) == 1) return(toupper(parts))
  last  <- toupper(parts[length(parts)])
  inits <- toupper(paste0(substr(parts[-length(parts)], 1, 1), collapse = ""))
  paste(last, inits)
}

.oa_ref_lines <- function(ref_ids, refs_dict = list()) {
  ref_ids <- ref_ids %||% list()
  lines <- character(0)
  for (rid in ref_ids) {
    short_id <- sub("^.*/", "", rid)
    info <- refs_dict[[short_id]]
    if (is.null(info)) {
      if (nzchar(short_id)) lines <- c(lines, short_id)
      next
    }
    line <- sprintf("%s, %s, %s", info$author_short,
                    if (!is.na(info$year)) info$year else "", info$journal_short)
    if (nzchar(info$volume))     line <- paste0(line, ", V", info$volume)
    if (nzchar(info$first_page)) line <- paste0(line, ", P", info$first_page)
    if (nzchar(info$doi))        line <- paste0(line, ", DOI ", info$doi)
    lines <- c(lines, line)
  }
  unique(lines[nzchar(lines)])
}

#' 单 work → WoS plaintext 记录块 (UT 必填: convert2df 去重主键)
.oa_work_to_wos_block <- function(work, refs_dict = list()) {
  authorships <- work$authorships %||% list()
  au_list <- character(0); af_list <- character(0)
  for (a in authorships) {
    name <- a$author$display_name %||% ""
    if (!nzchar(name)) next
    parts <- strsplit(name, "\\s+")[[1]]
    if (length(parts) == 1) {
      au_list <- c(au_list, toupper(name)); af_list <- c(af_list, name)
    } else {
      last  <- toupper(parts[length(parts)])
      inits <- toupper(paste0(substr(parts[-length(parts)], 1, 1), collapse = ""))
      au_list <- c(au_list, sprintf("%s %s", last, inits))
      af_list <- c(af_list, sprintf("%s, %s", parts[length(parts)],
                                     paste(parts[-length(parts)], collapse = " ")))
    }
  }
  title   <- toupper(.oa_clean_text(work$title %||% work$display_name %||% ""))
  journal <- toupper(.oa_clean_text(work$primary_location$source$display_name %||% ""))
  year    <- as.character(work$publication_year %||% "")
  issn    <- (work$primary_location$source$issn_l %||% "")
  doi     <- sub("^https?://(dx\\.)?doi\\.org/", "", work$doi %||% "", ignore.case = TRUE)
  tc      <- as.character(work$cited_by_count %||% 0L)
  ut      <- sub("^.*/", "", work$id %||% paste0("OA_", as.integer(Sys.time())))
  j9      <- substr(journal, 1, 30)
  volume  <- work$biblio$volume %||% ""
  issue   <- work$biblio$issue  %||% ""
  bp_pg   <- work$biblio$first_page %||% ""
  ep_pg   <- work$biblio$last_page  %||% ""

  abstract <- toupper(.oa_clean_text(.oa_inverted_to_abstract(work$abstract_inverted_index)))

  kws <- character(0)
  if (length(work$keywords)) {
    kws <- vapply(work$keywords, function(k) k$display_name %||% "", character(1))
  }
  if (!length(kws) && length(work$concepts)) {
    top <- utils::head(work$concepts, 5L)
    kws <- vapply(top, function(c) c$display_name %||% "", character(1))
  }
  kws <- vapply(kws[nzchar(kws)], .oa_clean_text, character(1), USE.NAMES = FALSE)
  de_field <- paste(kws, collapse = "; ")

  c1_lines <- character(0)
  for (a in authorships) {
    for (inst in (a$institutions %||% list())) {
      iname <- .oa_clean_text(inst$display_name %||% "")
      if (!nzchar(iname)) next
      c1_lines <- c(c1_lines, toupper(iname))
    }
  }
  c1_set <- unique(c1_lines)
  c3_str <- paste(c1_set, collapse = "; ")

  ref_ids <- work$referenced_works %||% list()
  cr_lines <- .oa_ref_lines(ref_ids, refs_dict)

  blk <- c("PT J")
  if (length(au_list)) blk <- c(blk, paste0("AU ", au_list[1]), paste0("   ", au_list[-1]))
  if (length(af_list)) blk <- c(blk, paste0("AF ", af_list[1]), paste0("   ", af_list[-1]))
  blk <- c(blk, paste0("TI ", title))
  if (nzchar(journal))  blk <- c(blk, paste0("SO ", journal))
  if (length(c1_set))   blk <- c(blk, paste0("C1 ", c1_set[1]), paste0("   ", c1_set[-1]))
  if (nzchar(c3_str))   blk <- c(blk, paste0("C3 ", c3_str))
  if (nzchar(de_field)) blk <- c(blk, paste0("DE ", de_field))
  if (nzchar(abstract)) blk <- c(blk, paste0("AB ", abstract))
  if (length(cr_lines)) blk <- c(blk, paste0("CR ", cr_lines[1]), paste0("   ", cr_lines[-1]))
  blk <- c(blk, "DT Article", "LA English")
  if (nzchar(j9))      blk <- c(blk, paste0("J9 ", j9))
  if (nzchar(volume))  blk <- c(blk, paste0("VL ", volume))
  if (nzchar(issue))   blk <- c(blk, paste0("IS ", issue))
  if (nzchar(bp_pg))   blk <- c(blk, paste0("BP ", bp_pg))
  if (nzchar(ep_pg))   blk <- c(blk, paste0("EP ", ep_pg))
  if (nzchar(issn))    blk <- c(blk, paste0("SN ", issn))
  if (nzchar(doi))     blk <- c(blk, paste0("DI ", doi))
  if (nzchar(year))    blk <- c(blk, paste0("PY ", year))
  blk <- c(blk, paste0("TC ", tc), paste0("UT WOS:", ut), "ER", "")
  paste(blk, collapse = "\n")
}

#' 一组 OpenAlex work → WoS plaintext 临时文件 (返回路径, 共用末段管线)
.oa_works_to_wos_file <- function(works, with_refs = TRUE) {
  if (!length(works)) return(NULL)
  refs_dict <- list()
  if (with_refs) {
    all_ref_ids <- unique(unlist(lapply(works, function(w) w$referenced_works %||% list())))
    refs_dict <- .oa_resolve_refs_batch(all_ref_ids)
  }
  blocks <- vapply(works, .oa_work_to_wos_block, character(1), refs_dict = refs_dict)
  wos_text <- paste0("FN Clarivate Analytics Web of Science\nVR 1.0\n",
                     paste(blocks, collapse = "\n"), "EF\n")
  tmp <- tempfile(pattern = "oa_corpus_", fileext = ".txt")
  writeLines(wos_text, tmp, useBytes = TRUE)
  tmp
}

#' OpenAlex works 列表 → 规范化候选列表 (纯映射, 不触网, 不建库)
#' 每个候选字段:
#'   openalexId, title, authors (chr vec), year, doi (去 URL 前缀),
#'   containerTitle, url, publicationDate, abstract, citedByCount, source="openalex"
.oa_works_to_candidates <- function(works) {
  if (!length(works)) return(list())
  # 检索候选只列标题/摘要, 无需解析引用题录; 跳过 .oa_resolve_refs_batch 的网络拉取
  # (单次 n=50 检索曾因此对 ~2500 个 ref id 串行批量拉 OpenAlex, 阻塞单线程 R ~60s,
  #  饿死 healthz → agent 5s 健康超时 → 前端"后端部分不可用"; 修后 60s→~2.6s)。
  # 候选的 references 字段改为直接返回原始 referenced_works ID（OpenAlex W-ID, 不触网），
  # 导入端 project.py _references_from_candidate 读 references → 写 csl_json.references。
  # 取舍（赛后 TODO）：from-search 导入后做引用计量(co-citation/histcite)时, CR 列会是
  # W-ID 而非格式化题录, bibliometrix 匹配质量下降; 真正的题录解析仍在建库路径
  # (.oa_works_to_wos_file, with_refs=TRUE) 完整保留。.oa_resolve_refs_batch 未删, 仍被它用。
  lapply(works, function(work) {
    # openalexId: 取 id URL 末段 (W...)
    oa_id <- sub("^.*/", "", work$id %||% "")

    # title: 优先 title 字段, 退到 display_name
    title <- work$title %||% work$display_name %||% ""

    # authors: 从 authorships 抽 display_name (保留原始大小写)
    authorships <- work$authorships %||% list()
    authors <- character(0)
    for (a in authorships) {
      nm <- a$author$display_name %||% ""
      if (nzchar(nm)) authors <- c(authors, nm)
    }

    # year
    year <- as.integer(work$publication_year %||% NA_integer_)

    # doi: 去掉 https://doi.org/ 或 https://dx.doi.org/ 前缀
    doi_raw <- work$doi %||% ""
    doi <- sub("^https?://(dx\\.)?doi\\.org/", "", doi_raw, ignore.case = TRUE)

    # containerTitle: 期刊/会议名称
    container_title <- .oa_clean_text(
      work$primary_location$source$display_name %||% ""
    )

    # url: 优先 doi 链接, 退到 OpenAlex 原始 id
    url <- if (nzchar(doi)) paste0("https://doi.org/", doi) else (work$id %||% "")

    # publicationDate
    pub_date <- work$publication_date %||% ""

    # abstract: 还原倒排索引
    abstract <- .oa_inverted_to_abstract(work$abstract_inverted_index)

    # keywords: OpenAlex 近年返回 keywords；旧记录回退到 concepts。
    kws <- character(0)
    if (length(work$keywords)) {
      kws <- vapply(work$keywords, function(k) k$display_name %||% "", character(1))
      kws <- kws[nzchar(trimws(kws))]
    }
    if (!length(kws) && length(work$concepts)) {
      top <- utils::head(work$concepts, 8L)
      kws <- vapply(top, function(c) c$display_name %||% "", character(1))
      kws <- kws[nzchar(trimws(kws))]
    }
    keywords <- paste(unique(kws), collapse = "; ")

    # citedByCount
    cited_by <- as.integer(work$cited_by_count %||% 0L)
    # 原始 referenced_works ID（不触网）；引用题录在建库时解析。
    references <- utils::head(work$referenced_works %||% list(), 50L)

    list(
      openalexId     = oa_id,
      title          = title,
      authors        = as.list(authors),
      year           = year,
      doi            = doi,
      containerTitle = container_title,
      url            = url,
      publicationDate = pub_date,
      abstract       = abstract,
      keywords       = keywords,
      references     = as.list(references),
      citedByCount   = cited_by,
      source         = "openalex"
    )
  })
}

#' 主题词检索 → 规范化候选列表 (只检索不建库)
#' 复用 .oa_search_works / .oa_works_to_candidates, 与 from-topic 路径并行存在互不干扰。
#' n 上限 clamp 至 100 (与 from-search 入库上限一致, 避免"检索成功但入库 422")。
#' 在网络/HTTP 错误时抛出含结构化信息的异常 (让调用方 plumber 处理成 502)。
oa_search_candidates <- function(query, n = 25, since = "2016-01-01") {
  if (is.null(query) || !nzchar(query)) stop("query 不能为空")
  n <- min(500L, max(1L, as.integer(n)))   # clamp: 1..500
  works <- .oa_search_works(query, n = n, since = since)
  # 检查错误信号 (网络/HTTP 错误与"真空结果"区分)
  if (.oa_is_error(works)) {
    stop(sprintf("OPENALEX_UNAVAILABLE|%d|%s",
                 works$status %||% 0L, works$message %||% "未知错误"))
  }
  .oa_works_to_candidates(works)
}

#' 主入口 (路径 A): 主题词 → WoS plaintext 文件路径 (NULL = 检索无果)
oa_build_wos_from_topic <- function(query, n = 50, since = "2016-01-01",
                                    with_refs = TRUE) {
  if (is.null(query) || !nzchar(query)) stop("query 不能为空")
  works <- .oa_search_works(query, n = n, since = since)
  if (!length(works)) return(NULL)
  .oa_works_to_wos_file(works, with_refs = with_refs)
}

#' 主入口 (路径 B): 结构化 papers (来自 agent 的 LLM 抽取) → OpenAlex 反查 →
#' WoS plaintext 文件路径。返回 list(path, matched, unmatched)。
#' papers: list of list(title, doi, ...)。
oa_build_wos_from_papers <- function(papers, with_refs = TRUE) {
  # plumber JSON 解析可能把 papers 简化成 data.frame, 统一归一为 list-of-list
  if (is.data.frame(papers)) {
    papers <- lapply(seq_len(nrow(papers)), function(i) as.list(papers[i, , drop = FALSE]))
  }
  if (!length(papers)) return(list(path = NULL, matched = 0L, unmatched = list()))
  works <- list(); unmatched <- list()
  for (p in papers) {
    title <- p$title %||% ""
    hit <- NULL
    # 路 1: DOI 精确 + 标题交叉校验 (防 LLM 给错 DOI 指向无关论文)
    if (nzchar(p$doi %||% "")) {
      w <- .oa_get_work_by_doi(p$doi)
      if (!is.null(w)) {
        sim <- if (nzchar(title)) .oa_title_sim(title, w$title %||% w$display_name %||% "") else 1
        if (sim >= 0.5) hit <- w
      }
    }
    # 路 2: 标题模糊 (≥ 0.7)
    if (is.null(hit) && nzchar(title)) {
      th <- .oa_search_by_title(title, sim_min = 0.7)
      if (!is.null(th)) hit <- th$work
    }
    if (!is.null(hit)) works[[length(works) + 1L]] <- hit
    else unmatched[[length(unmatched) + 1L]] <- list(title = title)
  }
  if (!length(works)) return(list(path = NULL, matched = 0L, unmatched = unmatched))
  list(path = .oa_works_to_wos_file(works, with_refs = with_refs),
       matched = length(works), unmatched = unmatched)
}
