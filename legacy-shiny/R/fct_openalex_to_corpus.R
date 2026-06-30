# fct_openalex_to_corpus.R — 主题词 → OpenAlex → bibliometrix corpus
#
# 战略意义: 把"完全没有 WoS/Scopus 数据"的新人门槛打掉.
# 用户输入一个主题词, 后台调 OpenAlex API 检索近 N 年文献, 还原摘要,
# 批量补全引用文献, 然后**写成 WoS plaintext 临时文件**让 bibliometrix::convert2df
# 处理 — 一致性最强, 衍生字段 (SR/AU_UN/KW_Merged) 全部由 bibliometrix 自动生成.
#
# 数据完整度: AU/TI/SO/PY/AB/DE/CR 全部补齐, 可跑完整套 bibliometrix 分析.
#
# OpenAlex polite pool: 所有请求带 mailto, 单 worker 限速 10 req/s.
#   email 取 OPENALEX_EMAIL 环境变量, 缺失时用项目默认值.
#
# 调用方: mod_welcome.R (路径 A 主题词搜索卡片)

OA_BASE         <- "https://api.openalex.org"
OA_DEFAULT_MAIL <- "aria-review@users.noreply.github.com"  # lit_pipeline.py 同款, 学术礼貌联系人
OA_PAGE_SIZE    <- 200L     # OpenAlex 单页上限
OA_REFS_BATCH   <- 50L      # filter=ids.openalex:W1|W2|... 单次 ID 数, 保守取 50
OA_TIMEOUT_S    <- 30

#' 清洗 OpenAlex 文本: 去 HTML 标签 / 实体 / 控制字符
#'
#' OpenAlex 标题/摘要常含 <scp>/<i>/<sub> 等 (出版商 XML 原文), 还有
#' &amp;/&#xnnn; 等实体. WoS plaintext 不允许这些, bibliometrix 也无法解析.
.oa_clean_text <- function(s) {
  if (!nzchar(s)) return("")
  s <- gsub("<[^>]+>", " ", s, perl = TRUE)        # HTML 标签
  s <- gsub("&[a-zA-Z]+;", " ", s, perl = TRUE)    # 命名实体
  s <- gsub("&#x?[0-9a-fA-F]+;", " ", s, perl = TRUE)  # 数值实体
  s <- gsub("[[:cntrl:]]", " ", s)                  # 控制字符
  s <- gsub("\\s+", " ", s)                          # 折叠空白
  trimws(s)
}

#' OpenAlex polite pool 用的联系邮箱
.oa_mail <- function() {
  v <- Sys.getenv("OPENALEX_EMAIL", unset = "")
  if (nzchar(v)) v else OA_DEFAULT_MAIL
}

#' 还原 OpenAlex abstract_inverted_index → 原文
#'
#' OpenAlex 出于版权把摘要存成倒排索引: {"word": [pos1, pos2,...], ...}.
#' 按 pos 升序重组成原始词序. NULL/empty → "".
#' 用 vector 累加替代 rbind, 避免 JSON 解析后位置类型混杂导致的 rbind 失败.
.oa_inverted_to_abstract <- function(inv_idx) {
  if (is.null(inv_idx) || length(inv_idx) == 0) return("")
  words <- character(0)
  positions <- integer(0)
  for (w in names(inv_idx)) {
    pos <- inv_idx[[w]]
    if (!length(pos)) next
    # 强制 integer (JSON 解析后可能是 list/numeric, 统一类型)
    pos_int <- suppressWarnings(as.integer(unlist(pos)))
    pos_int <- pos_int[!is.na(pos_int)]
    if (!length(pos_int)) next
    words     <- c(words, rep(w, length(pos_int)))
    positions <- c(positions, pos_int)
  }
  if (!length(words)) return("")
  paste(words[order(positions)], collapse = " ")
}

#' 标题相似度 (0-1), 用于把关按标题匹配的稳健性.
#'
#' 实现策略: 标题归一化 (小写, 去除标点空白) 后用 R 自带 adist 计算编辑
#' 距离, 转成 0-1 相似度. 不依赖 stringdist 包.
.oa_title_sim <- function(a, b) {
  norm <- function(s) gsub("[^a-z0-9]", "", tolower(s %||% ""))
  na <- norm(a); nb <- norm(b)
  if (!nzchar(na) || !nzchar(nb)) return(0)
  d <- utils::adist(na, nb)[1, 1]
  1 - d / max(nchar(na), nchar(nb))
}

#' 按 DOI 取单条 OpenAlex work
#'
#' @return work list 或 NULL (未找到)
.oa_get_work_by_doi <- function(doi) {
  if (!nzchar(doi)) return(NULL)
  # 标准化 DOI: 去 https://doi.org/ 前缀
  doi <- sub("^https?://(dx\\.)?doi\\.org/", "", doi, ignore.case = TRUE)
  req <- httr2::request(sprintf("%s/works/doi:%s", OA_BASE, doi)) |>
    httr2::req_url_query(mailto = .oa_mail()) |>
    httr2::req_timeout(OA_TIMEOUT_S) |>
    httr2::req_user_agent(sprintf("BiblioCN/0.1 (mailto:%s)", .oa_mail())) |>
    httr2::req_error(is_error = function(resp) FALSE)  # 404 不报错, 自己处理
  resp <- tryCatch(httr2::req_perform(req), error = function(e) NULL)
  if (is.null(resp) || httr2::resp_status(resp) >= 400) return(NULL)
  httr2::resp_body_json(resp, simplifyVector = FALSE)
}

#' 按标题搜索 OpenAlex, 返回相似度最高的 work (≥ sim_min 才接受)
#'
#' @return list(work, sim) 或 NULL (未匹配)
.oa_search_by_title <- function(title, sim_min = 0.7) {
  if (!nzchar(title)) return(NULL)
  req <- httr2::request(paste0(OA_BASE, "/works")) |>
    httr2::req_url_query(
      filter    = sprintf("title.search:%s", title),
      `per-page`= 5L,
      mailto    = .oa_mail()
    ) |>
    httr2::req_timeout(OA_TIMEOUT_S) |>
    httr2::req_user_agent(sprintf("BiblioCN/0.1 (mailto:%s)", .oa_mail()))
  resp <- tryCatch(httr2::req_perform(req), error = function(e) NULL)
  if (is.null(resp)) return(NULL)
  body <- httr2::resp_body_json(resp, simplifyVector = FALSE)
  results <- body$results %||% list()
  if (!length(results)) return(NULL)
  # 取相似度最高的
  best <- NULL; best_sim <- 0
  for (w in results) {
    sim <- .oa_title_sim(title, w$title %||% w$display_name %||% "")
    if (sim > best_sim) { best_sim <- sim; best <- w }
  }
  if (best_sim < sim_min) return(NULL)
  list(work = best, sim = best_sim)
}

#' 把一组 OpenAlex work 直接组装成 corpus (跳过主题搜索, 用于路径 B)
#'
#' 给定已经拿到的 work 列表, 复用 .oa_work_to_wos_block + convert2df.
#' 与 oa_corpus_from_topic 共用末段管线, 保证一致性.
#'
#' @param works         list of OpenAlex work objects
#' @param with_refs     是否补全引用
#' @param on_progress   进度回调
oa_corpus_from_works <- function(works,
                                   with_refs = TRUE,
                                   on_progress = function(...) NULL) {
  if (!length(works)) return(NULL)
  refs_dict <- list()
  if (with_refs) {
    all_ref_ids <- unique(unlist(lapply(works,
                                          function(w) w$referenced_works %||% list())))
    on_progress(stage = "refs", done = 0,
                total = ceiling(length(all_ref_ids) / OA_REFS_BATCH),
                msg = sprintf("补全 %d 条引用题录...", length(all_ref_ids)))
    refs_dict <- .oa_resolve_refs_batch(all_ref_ids, on_progress = on_progress)
  }
  on_progress(stage = "compose", done = 0, total = length(works),
              msg = "组装 WoS 格式...")
  blocks <- vapply(works, .oa_work_to_wos_block, character(1),
                    refs_dict = refs_dict)
  wos_text <- paste0("FN Clarivate Analytics Web of Science\nVR 1.0\n",
                     paste(blocks, collapse = "\n"), "EF\n")
  tmp <- tempfile(pattern = "oa_corpus_", fileext = ".txt")
  writeLines(wos_text, tmp, useBytes = TRUE)
  on_progress(stage = "parse", done = 0, total = 1,
              msg = "解析为 bibliometrix corpus...")
  M <- tryCatch(
    import_corpus(tmp, dbsource = "wos", format = "plaintext"),
    error = function(e) {
      warning(sprintf("convert2df 失败: %s", conditionMessage(e)))
      NULL
    }
  )
  unlink(tmp)
  on_progress(stage = "done", done = 1, total = 1,
              msg = sprintf("完成: %s 条",
                             if (is.null(M)) "0" else as.character(nrow(M))))
  M
}

#' OpenAlex 主题词检索, 按相关度返回近 since 年发表的 n 篇文献
#'
#' 直接抓取 work 完整对象 (含 abstract_inverted_index / referenced_works /
#' authorships / concepts / biblio), 一次调用一次满载, 避免后续逐篇回查.
#'
#' @return list of work objects (每个是 OpenAlex /works 返回的 work)
.oa_search_works <- function(query, n = 50, since = "2015-01-01",
                              on_progress = function(...) NULL) {
  works <- list()
  cursor <- "*"
  # 单次最多取 OA_PAGE_SIZE; 若 n 小, 用 n 减少响应体
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
      httr2::req_user_agent(sprintf("BiblioCN/0.1 (mailto:%s)", .oa_mail()))
    resp <- tryCatch(httr2::req_perform(req), error = function(e) {
      warning(sprintf("OpenAlex 搜索失败: %s", conditionMessage(e)))
      NULL
    })
    if (is.null(resp)) break
    body <- httr2::resp_body_json(resp, simplifyVector = FALSE)
    results <- body$results %||% list()
    if (!length(results)) break
    # 截断到 n 篇上限, 不要一次性塞超过用户请求量
    need <- n - length(works)
    works <- c(works, utils::head(results, need))
    on_progress(stage = "search", done = length(works), total = n,
                msg = sprintf("已检索 %d 篇", length(works)))
    if (length(works) >= n) break
    cursor <- body$meta$next_cursor
  }
  works
}

#' 批量取 referenced works 的题录信息 (用于组装 CR 字段)
#'
#' 给一个 OpenAlex work ID 列表 (形如 https://openalex.org/W123), 批量调
#' /works?filter=ids.openalex:W1|W2|... 一次拉多条. 返回以 OpenAlex ID 为 key
#' 的查找表, value 是 list(author_short, year, journal_short, doi).
#'
#' OpenAlex 单次 filter 上限保守取 50; 调用方在主流程里聚合所有 work 的
#' referenced_works ID, 去重后批量查, 比逐 work 调用快 ~30x.
.oa_resolve_refs_batch <- function(ref_ids,
                                    on_progress = function(...) NULL) {
  if (!length(ref_ids)) return(list())
  ref_ids <- unique(ref_ids)
  # 提取末尾的 Wxxx ID, OpenAlex filter 用纯 ID
  short_ids <- sub("^.*/(W[0-9]+)$", "\\1", ref_ids)
  short_ids <- short_ids[grepl("^W[0-9]+$", short_ids)]
  if (!length(short_ids)) return(list())

  # 分批调用
  out <- list()
  batches <- split(short_ids, ceiling(seq_along(short_ids) / OA_REFS_BATCH))
  for (i in seq_along(batches)) {
    chunk <- batches[[i]]
    req <- httr2::request(paste0(OA_BASE, "/works")) |>
      httr2::req_url_query(
        filter    = paste0("ids.openalex:", paste(chunk, collapse = "|")),
        `per-page`= OA_REFS_BATCH,
        # 只取需要的字段, 减少响应体
        select    = "id,doi,publication_year,authorships,primary_location,biblio",
        mailto    = .oa_mail()
      ) |>
      httr2::req_timeout(OA_TIMEOUT_S) |>
      httr2::req_user_agent(sprintf("BiblioCN/0.1 (mailto:%s)", .oa_mail()))
    resp <- tryCatch(httr2::req_perform(req), error = function(e) {
      warning(sprintf("OpenAlex 引用查询批 %d 失败: %s", i, conditionMessage(e)))
      NULL
    })
    on_progress(stage = "refs", done = i, total = length(batches),
                msg = sprintf("引用题录 %d/%d 批", i, length(batches)))
    if (is.null(resp)) next
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

#' 从 authorships 列表取第一作者的 "LASTNAME FI" 短形式 (WoS CR 用)
.oa_first_author_short <- function(authorships) {
  if (!length(authorships)) return("ANON")
  a1 <- authorships[[1]]$author$display_name %||% ""
  if (!nzchar(a1)) return("ANON")
  # "Foo Bar Baz" → "BAZ FB" (姓 + 名首字母 +首字母)
  parts <- strsplit(a1, "\\s+")[[1]]
  if (length(parts) == 1) return(toupper(parts))
  last  <- toupper(parts[length(parts)])
  inits <- toupper(paste0(substr(parts[-length(parts)], 1, 1), collapse = ""))
  paste(last, inits)
}

#' 单 work → WoS plaintext 记录块
#'
#' 输出格式 (FN/VR 头 + ER/EF 尾在主流程拼接, 这里只产单条记录主体):
#'   PT J
#'   AU LASTNAME FI
#'      LASTNAME2 FI2
#'   AF Lastname, Firstname
#'   TI title
#'   SO JOURNAL NAME
#'   ...
#'   CR ref1
#'      ref2
#'   ER
#'
#' refs_dict: .oa_resolve_refs_batch() 返回的 ID→题录映射 (用于 CR 字段)
.oa_work_to_wos_block <- function(work, refs_dict = list()) {
  # ---- 作者 AU (LASTNAME FI 格式, 全大写) ----
  authorships <- work$authorships %||% list()
  au_list <- character(0)
  af_list <- character(0)
  for (a in authorships) {
    name <- a$author$display_name %||% ""
    if (!nzchar(name)) next
    parts <- strsplit(name, "\\s+")[[1]]
    if (length(parts) == 1) {
      au_list <- c(au_list, toupper(name))
      af_list <- c(af_list, name)
    } else {
      last  <- toupper(parts[length(parts)])
      inits <- toupper(paste0(substr(parts[-length(parts)], 1, 1), collapse = ""))
      au_list <- c(au_list, sprintf("%s %s", last, inits))
      af_list <- c(af_list, sprintf("%s, %s", parts[length(parts)],
                                     paste(parts[-length(parts)], collapse = " ")))
    }
  }

  # ---- 标题 / 期刊 / 年份 (统一清洗 HTML/实体/控制字符) ----
  title   <- toupper(.oa_clean_text(work$title %||% work$display_name %||% ""))
  journal <- toupper(.oa_clean_text(work$primary_location$source$display_name %||% ""))
  year    <- as.character(work$publication_year %||% "")
  issn    <- (work$primary_location$source$issn_l %||% "")
  doi     <- sub("^https?://(dx\\.)?doi\\.org/", "",
                 work$doi %||% "", ignore.case = TRUE)
  tc      <- as.character(work$cited_by_count %||% 0L)
  # WoS UT (unique tag) — bibliometrix convert2df 用它做去重的主键, 必填.
  # 我们用 OpenAlex ID 的短码 (Wxxx) 替代, 保证唯一性.
  ut      <- sub("^.*/", "", work$id %||% paste0("OA_", as.integer(Sys.time())))
  # J9 = abbreviated source title (WoS short form). 用 SO 取前 30 字符兜底,
  # 让 bibliometrix 的 SR 字段 "Author, Year, Journal" 不至于尾部空白.
  j9      <- substr(journal, 1, 30)
  volume  <- work$biblio$volume %||% ""
  issue   <- work$biblio$issue  %||% ""
  bp_pg   <- work$biblio$first_page %||% ""
  ep_pg   <- work$biblio$last_page  %||% ""

  # ---- 摘要还原 ----
  abstract <- .oa_inverted_to_abstract(work$abstract_inverted_index)
  abstract <- toupper(.oa_clean_text(abstract))

  # ---- 关键词: 优先 keywords, 没有时用 concepts top-5 ----
  kws <- character(0)
  if (length(work$keywords)) {
    kws <- vapply(work$keywords, function(k) k$display_name %||% "",
                  character(1))
  }
  if (!length(kws) && length(work$concepts)) {
    # concepts 已按 score 降序, 取前 5 个高分概念作为 KeyWords Plus
    top <- utils::head(work$concepts, 5L)
    kws <- vapply(top, function(c) c$display_name %||% "", character(1))
  }
  kws <- vapply(kws[nzchar(kws)], .oa_clean_text, character(1), USE.NAMES = FALSE)
  de_field <- paste(kws, collapse = "; ")

  # ---- 机构 / 国家 ----
  c1_lines <- character(0)
  c3_set   <- character(0)
  for (a in authorships) {
    for (inst in (a$institutions %||% list())) {
      iname <- .oa_clean_text(inst$display_name %||% "")
      if (!nzchar(iname)) next
      c1_lines <- c(c1_lines, toupper(iname))
      c3_set   <- c(c3_set, toupper(iname))
    }
  }
  c1_set <- unique(c1_lines)
  c3_str <- paste(unique(c3_set), collapse = "; ")

  # ---- 参考文献 CR ----
  ref_ids <- work$referenced_works %||% list()
  cr_lines <- character(0)
  for (rid in ref_ids) {
    key <- sub("^.*/", "", rid)
    info <- refs_dict[[key]]
    if (is.null(info)) next
    line <- sprintf("%s, %s, %s",
                    info$author_short,
                    if (!is.na(info$year)) info$year else "",
                    info$journal_short)
    if (nzchar(info$volume))     line <- paste0(line, ", V", info$volume)
    if (nzchar(info$first_page)) line <- paste0(line, ", P", info$first_page)
    if (nzchar(info$doi))        line <- paste0(line, ", DOI ", info$doi)
    cr_lines <- c(cr_lines, line)
  }

  # ---- 拼装 WoS plaintext 块 ----
  blk <- c("PT J")
  if (length(au_list)) {
    blk <- c(blk, paste0("AU ", au_list[1]),
              paste0("   ", au_list[-1]))
  }
  if (length(af_list)) {
    blk <- c(blk, paste0("AF ", af_list[1]),
              paste0("   ", af_list[-1]))
  }
  blk <- c(blk, paste0("TI ", title))
  if (nzchar(journal))  blk <- c(blk, paste0("SO ", journal))
  if (length(c1_set))   blk <- c(blk, paste0("C1 ", c1_set[1]),
                                  paste0("   ", c1_set[-1]))
  if (nzchar(c3_str))   blk <- c(blk, paste0("C3 ", c3_str))
  if (nzchar(de_field)) blk <- c(blk, paste0("DE ", de_field))
  if (nzchar(abstract)) blk <- c(blk, paste0("AB ", abstract))
  if (length(cr_lines)) blk <- c(blk, paste0("CR ", cr_lines[1]),
                                  paste0("   ", cr_lines[-1]))
  blk <- c(blk, "DT Article", "LA English")
  if (nzchar(j9))      blk <- c(blk, paste0("J9 ", j9))
  if (nzchar(volume))  blk <- c(blk, paste0("VL ", volume))
  if (nzchar(issue))   blk <- c(blk, paste0("IS ", issue))
  if (nzchar(bp_pg))   blk <- c(blk, paste0("BP ", bp_pg))
  if (nzchar(ep_pg))   blk <- c(blk, paste0("EP ", ep_pg))
  if (nzchar(issn))    blk <- c(blk, paste0("SN ", issn))
  if (nzchar(doi))     blk <- c(blk, paste0("DI ", doi))
  if (nzchar(year))    blk <- c(blk, paste0("PY ", year))
  blk <- c(blk, paste0("TC ", tc))
  # UT 必填: bibliometrix 去重主键, 缺失会让 convert2df 抛 undefined columns selected
  blk <- c(blk, paste0("UT WOS:", ut))
  blk <- c(blk, "ER", "")
  paste(blk, collapse = "\n")
}

#' 主入口: 主题词 → bibliometrix corpus
#'
#' 流程: search → 还原摘要 → 收集所有 referenced_works → 批量补题录 →
#' 写 WoS plaintext 临时文件 → convert2df → 返回 M.
#'
#' @param query      主题词 (中英均可, OpenAlex 全文索引)
#' @param n          取多少篇主论文 (默认 50, 上限自定, 50 篇约 10~30s)
#' @param since      "YYYY-MM-DD", 发表日下限 (默认近十年)
#' @param with_refs  TRUE = 同步补全引用题录 (慢但完整); FALSE = CR 留空 (快)
#' @param on_progress function(stage, done, total, msg) 回调, Shiny 端接进度条
#'
#' @return data.frame (bibliometrix M) 或 NULL (检索失败)
oa_corpus_from_topic <- function(query, n = 50, since = "2015-01-01",
                                  with_refs = TRUE,
                                  on_progress = function(...) NULL) {
  if (!nzchar(query)) stop("query 不能为空")

  # 1. 主论文检索
  on_progress(stage = "search", done = 0, total = n,
              msg = sprintf("OpenAlex 检索 '%s' (近 %s)", query, since))
  works <- .oa_search_works(query, n = n, since = since,
                             on_progress = on_progress)
  if (!length(works)) {
    warning("OpenAlex 检索无结果")
    return(NULL)
  }

  # 2. 批量解 references (如启用)
  refs_dict <- list()
  if (with_refs) {
    all_ref_ids <- unique(unlist(lapply(works,
                                         function(w) w$referenced_works %||% list())))
    on_progress(stage = "refs", done = 0,
                total = ceiling(length(all_ref_ids) / OA_REFS_BATCH),
                msg = sprintf("补全 %d 条引用题录...", length(all_ref_ids)))
    refs_dict <- .oa_resolve_refs_batch(all_ref_ids, on_progress = on_progress)
  }

  # 3. 组装 WoS plaintext 全文
  on_progress(stage = "compose", done = 0, total = length(works),
              msg = "组装 WoS 格式...")
  blocks <- character(length(works))
  for (i in seq_along(works)) {
    blocks[i] <- .oa_work_to_wos_block(works[[i]], refs_dict)
    if (i %% 10 == 0) {
      on_progress(stage = "compose", done = i, total = length(works),
                  msg = sprintf("组装中 %d/%d", i, length(works)))
    }
  }
  wos_text <- paste0("FN Clarivate Analytics Web of Science\nVR 1.0\n",
                     paste(blocks, collapse = "\n"),
                     "EF\n")

  # 4. 临时文件 → convert2df
  tmp <- tempfile(pattern = "oa_corpus_", fileext = ".txt")
  writeLines(wos_text, tmp, useBytes = TRUE)
  on_progress(stage = "parse", done = 0, total = 1,
              msg = "解析为 bibliometrix corpus...")
  M <- tryCatch(
    import_corpus(tmp, dbsource = "wos", format = "plaintext"),
    error = function(e) {
      # 失败时保留临时文件 + 路径到 warning, 便于排查
      keep <- sub("\\.txt$", "_DEBUG.txt", tmp)
      file.copy(tmp, keep, overwrite = TRUE)
      warning(sprintf("convert2df 失败: %s  [调试样本保留于: %s]",
                       conditionMessage(e), keep))
      NULL
    }
  )
  unlink(tmp)
  on_progress(stage = "done", done = 1, total = 1,
              msg = sprintf("完成: %s 条记录",
                             if (is.null(M)) "0" else as.character(nrow(M))))
  M
}

# %||% 在 fct_cite.R 等已定义, 这里不再重复定义 (避免覆盖)
