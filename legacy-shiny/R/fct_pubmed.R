# R/fct_pubmed.R — PubMed E-utilities 接入 → bibliometrix corpus
#
# 战略意义: 把 PubMed (PMID / .nbib / 检索式) 三种入口统一转成
# bibliometrix corpus data.frame, 与 WoS/Scopus/OpenAlex corpus 等价可合并.
#
# 一致性策略 (与 fct_openalex_to_corpus.R 同款思路):
#   不自己手搓 corpus data.frame, 而是把 PubMed 数据**还原成 MEDLINE 纯文本**,
#   再交给 bibliometrix::convert2df(dbsource="pubmed", format="pubmed") 解析.
#   这样字段名/类型/格式 (AU 用 ";" 分隔大写 "姓+名首字母", PY 为 numeric,
#   以及衍生字段 AU_UN/SR/KW_Merged 等) 全部由 bibliometrix 自动生成,
#   与其他来源 corpus 完全一致, biblioAnalysis() 不会报错.
#
#   convert2df 产出的 corpus 主键列是 PMID. spec §4.4 额外要求把 PMID 映射到
#   UT (作为 ID) 与 PM 两列, 故解析后补写这两列 (不覆盖 bibliometrix 原有列).
#
# 字段映射 (spec §4.4, PubMed MEDLINE → bibliometrix):
#   PMID → UT + PM (本文件补) / PMID (bibliometrix 原生)
#   TI   → TI        AB → AB
#   AU   → AU (";" 分隔)   AD → C1 (机构)   JT/TA → SO/J9
#   DP   → PY (年份, numeric)   MH → DE (";" 分隔)
#   LID/AID 里的 DOI → DI       PT → DT
#
# E-utilities 端点 (NCBI):
#   esearch: …/esearch.fcgi?db=pubmed&term=<query>&retmax=<n>&retmode=json
#   efetch:  …/efetch.fcgi?db=pubmed&id=<pmids>&retmode=xml
#   限流: 无 key 3 req/s, 有 key 10 req/s. 必带 User-Agent + timeout + tryCatch 降级.
#
# KISS: 不做缓存, 不做超过 max_records 的分页; 网络/解析失败一律降级返回 NULL.
#
# 调用方: mod_upload.R (PubMed 入口的三种交互)

EUTILS_BASE       <- "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_TIMEOUT_S  <- 60L     # efetch 大批量可能慢, 给足超时
PUBMED_UA         <- "BiblioCN/0.6"

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

#' 当前请求该用的限流速率 (req/s)
#'
#' NCBI 政策: 无 key 3 req/s, 有 key 10 req/s. 留 1 req/s 余量更稳.
.pubmed_rate <- function(api_key = NULL) {
  if (!is.null(api_key) && nzchar(api_key)) 9 else 2
}

#' 把 query 规整为"是否纯 PMID 向量"
#'
#' 接受: 字符/数值向量, 或单串内逗号/换行/空格分隔的多个 PMID.
#' 若全部 token 都是纯数字 → 视为 PMID 列表, 返回去重后的字符向量;
#' 否则返回 NULL (表示这是一条检索式, 需走 esearch).
.pubmed_as_pmids <- function(query) {
  if (is.null(query) || length(query) == 0L) return(NULL)
  toks <- unlist(strsplit(as.character(query), "[,;\\s]+", perl = TRUE))
  toks <- trimws(toks)
  toks <- toks[nzchar(toks)]
  if (!length(toks)) return(NULL)
  if (all(grepl("^[0-9]+$", toks))) unique(toks) else NULL
}

#' 构造一个带统一约定 (UA / timeout / 限流 / 不抛 HTTP 错) 的 httr2 请求
.pubmed_request <- function(url, api_key = NULL) {
  req <- httr2::request(url) |>
    httr2::req_user_agent(PUBMED_UA) |>
    httr2::req_timeout(PUBMED_TIMEOUT_S) |>
    httr2::req_throttle(rate = .pubmed_rate(api_key)) |>
    httr2::req_error(is_error = function(resp) FALSE)  # HTTP 错误自己处理, 不抛
  req
}

#' esearch: 检索式 → PMID 向量
#'
#' @return character 向量 (PMID); 失败或无结果返回 character(0)
.pubmed_esearch <- function(query, max_records = 200L, api_key = NULL) {
  if (is.null(query) || !nzchar(query)) return(character(0))
  url <- sprintf("%s/esearch.fcgi", EUTILS_BASE)
  req <- .pubmed_request(url, api_key) |>
    httr2::req_url_query(
      db      = "pubmed",
      term    = query,
      retmax  = as.integer(max_records),
      retmode = "json"
    )
  if (!is.null(api_key) && nzchar(api_key)) {
    req <- httr2::req_url_query(req, api_key = api_key)
  }
  resp <- tryCatch(httr2::req_perform(req), error = function(e) {
    warning(sprintf("PubMed esearch 失败: %s", conditionMessage(e)))
    NULL
  })
  if (is.null(resp) || httr2::resp_status(resp) >= 400) return(character(0))
  body <- tryCatch(httr2::resp_body_json(resp, simplifyVector = TRUE),
                   error = function(e) NULL)
  ids <- body$esearchresult$idlist %||% character(0)
  as.character(ids)
}

#' efetch: PMID 向量 → efetch XML 原文 (PubmedArticleSet)
#'
#' @return character(1) XML 文本; 失败返回 ""
.pubmed_efetch <- function(pmids, api_key = NULL) {
  if (!length(pmids)) return("")
  url <- sprintf("%s/efetch.fcgi", EUTILS_BASE)
  req <- .pubmed_request(url, api_key) |>
    httr2::req_url_query(
      db      = "pubmed",
      id      = paste(pmids, collapse = ","),
      retmode = "xml"
    )
  if (!is.null(api_key) && nzchar(api_key)) {
    req <- httr2::req_url_query(req, api_key = api_key)
  }
  resp <- tryCatch(httr2::req_perform(req), error = function(e) {
    warning(sprintf("PubMed efetch 失败: %s", conditionMessage(e)))
    NULL
  })
  if (is.null(resp) || httr2::resp_status(resp) >= 400) return("")
  tryCatch(httr2::resp_body_string(resp), error = function(e) "")
}

# ---------------------------------------------------------------------------
# XML / MEDLINE 解析
# ---------------------------------------------------------------------------

#' 取节点下指定 xpath 的全部文本 (trim, 去空)
.xml_texts <- function(node, xpath) {
  nodes <- tryCatch(xml2::xml_find_all(node, xpath),
                    error = function(e) xml2::xml_find_all(node, "/.."))
  if (!length(nodes)) return(character(0))
  v <- trimws(xml2::xml_text(nodes))
  v[nzchar(v)]
}

#' 取节点下指定 xpath 的第一个文本 ("" 兜底)
.xml_text1 <- function(node, xpath) {
  v <- .xml_texts(node, xpath)
  if (length(v)) v[1] else ""
}

#' 单个 <PubmedArticle> 节点 → 一段 MEDLINE 纯文本记录
#'
#' 输出与 PubMed "Send to → File → MEDLINE (.nbib)" 一致的 "TAG - content" 行格式,
#' 续行用 6 空格缩进; 这样可直接喂给 bibliometrix::pubmed2df.
#' 仅产出 pubmed2df 关心 + spec §4.4 要求的标签, 其余忽略 (KISS).
.efetch_node_to_medline <- function(art) {
  lines <- character(0)
  add <- function(tag, val) {
    val <- trimws(val)
    if (!nzchar(val)) return(invisible())
    # 续行 (含换行) 折叠为单空格, 避免破坏 MEDLINE 行结构
    val <- gsub("\\s+", " ", val)
    lines[[length(lines) + 1L]] <<- sprintf("%-4s- %s", tag, val)
  }

  # ---- PMID (主键) ----
  pmid <- .xml_text1(art, ".//MedlineCitation/PMID")
  if (!nzchar(pmid)) pmid <- .xml_text1(art, ".//PMID")
  add("PMID", pmid)

  # ---- 标题 TI ----
  add("TI", .xml_text1(art, ".//Article/ArticleTitle"))

  # ---- 摘要 AB (可能多段 AbstractText, 拼接) ----
  ab <- .xml_texts(art, ".//Article/Abstract/AbstractText")
  if (length(ab)) add("AB", paste(ab, collapse = " "))

  # ---- 作者 AU/FAU + 机构 AD ----
  authors <- xml2::xml_find_all(art, ".//Article/AuthorList/Author")
  for (a in authors) {
    last  <- .xml_text1(a, "./LastName")
    fore  <- .xml_text1(a, "./ForeName")
    init  <- .xml_text1(a, "./Initials")
    coll  <- .xml_text1(a, "./CollectiveName")
    if (nzchar(last)) {
      # FAU = "Last, ForeName"; AU = "Last Initials" (pubmed2df 用 AU 生成 corpus AU)
      fau <- if (nzchar(fore)) paste0(last, ", ", fore) else last
      au  <- if (nzchar(init)) paste(last, init) else last
      add("FAU", fau)
      add("AU", au)
    } else if (nzchar(coll)) {
      add("FAU", coll)
      add("AU", coll)
    }
    # 机构 AD (每作者可多个 Affiliation)
    for (aff in .xml_texts(a, "./AffiliationInfo/Affiliation")) add("AD", aff)
  }

  # ---- 期刊 JT/TA → SO/J9 ----
  jt <- .xml_text1(art, ".//Article/Journal/Title")
  ta <- .xml_text1(art, ".//Article/Journal/ISOAbbreviation")
  if (!nzchar(ta)) ta <- .xml_text1(art, ".//MedlineJournalInfo/MedlineTA")
  add("JT", jt)
  add("TA", ta)

  # ---- ISSN ----
  add("IS", .xml_text1(art, ".//Article/Journal/ISSN"))

  # ---- 出版日期 DP (PubDate Year (+Month)) → pubmed2df 取前 4 位为 PY ----
  py <- .xml_text1(art, ".//Article/Journal/JournalIssue/PubDate/Year")
  if (!nzchar(py)) {
    medline_dt <- .xml_text1(art, ".//Article/Journal/JournalIssue/PubDate/MedlineDate")
    py <- sub("^.*?([0-9]{4}).*$", "\\1", medline_dt)
    if (py == medline_dt) py <- ""  # 没抽到 4 位年份
  }
  mon <- .xml_text1(art, ".//Article/Journal/JournalIssue/PubDate/Month")
  if (nzchar(py)) add("DP", trimws(paste(py, mon)))

  # ---- 卷期页 VI/IP/PG ----
  add("VI", .xml_text1(art, ".//Article/Journal/JournalIssue/Volume"))
  add("IP", .xml_text1(art, ".//Article/Journal/JournalIssue/Issue"))
  add("PG", .xml_text1(art, ".//Article/Pagination/MedlinePgn"))

  # ---- MeSH MH → DE (关键词) ----
  for (mh in .xml_texts(art, ".//MeshHeadingList/MeshHeading/DescriptorName")) {
    add("MH", mh)
  }

  # ---- 出版类型 PT → DT ----
  for (pt in .xml_texts(art, ".//PublicationTypeList/PublicationType")) {
    add("PT", pt)
  }

  # ---- DOI: ELocationID[@EIdType='doi'] / ArticleId[@IdType='doi'] → LID/AID ----
  doi <- .xml_text1(art, ".//ELocationID[@EIdType='doi']")
  if (!nzchar(doi)) doi <- .xml_text1(art, ".//ArticleIdList/ArticleId[@IdType='doi']")
  if (nzchar(doi)) {
    # pubmed2df: df$DI 取 LID 中 "[" 前的内容; AID 也保留
    add("LID", paste0(doi, " [doi]"))
    add("AID", paste0(doi, " [doi]"))
  }

  paste(lines, collapse = "\n")
}

#' 给 corpus 补 spec §4.4 要求的 PM / UT 列 (PMID 同源)
#'
#' bibliometrix::pubmed2df 产出的主键列名是 PMID; spec 要求额外暴露 PM 与 UT.
#' 复制而非重命名, 保证两边 (bibliometrix 内部 + spec 约定) 都满足.
.pubmed_add_id_cols <- function(M) {
  if (!is.data.frame(M) || !nrow(M)) return(M)
  pmid <- if ("PMID" %in% names(M)) as.character(M$PMID) else NA_character_
  if (!"PM" %in% names(M)) M$PM <- pmid
  if (!"UT" %in% names(M)) M$UT <- pmid
  M
}

#' efetch XML 文本 → bibliometrix corpus (纯函数, 便于喂合成 XML 单测)
#'
#' 流程: xml2 解析 → 逐 PubmedArticle 还原 MEDLINE 块 → 写临时文件 →
#'       import_corpus(dbsource="pubmed") → 补 PM/UT 列.
#' 任意环节失败 → 返回 NULL (空 corpus 不崩溃).
#'
#' @param xml_text character(1); efetch retmode=xml 的响应原文 (PubmedArticleSet)
#' @return data.frame (bibliometrix corpus) 或 NULL
.parse_efetch_xml <- function(xml_text) {
  if (is.null(xml_text) || !nzchar(xml_text)) return(NULL)
  doc <- tryCatch(xml2::read_xml(xml_text), error = function(e) {
    warning(sprintf("efetch XML 解析失败: %s", conditionMessage(e)))
    NULL
  })
  if (is.null(doc)) return(NULL)
  arts <- xml2::xml_find_all(doc, "//PubmedArticle")
  if (!length(arts)) return(NULL)

  blocks <- vapply(arts, .efetch_node_to_medline, character(1))
  blocks <- blocks[nzchar(blocks)]
  if (!length(blocks)) return(NULL)

  medline_text <- paste(blocks, collapse = "\n\n")
  tmp <- tempfile(pattern = "pubmed_", fileext = ".nbib")
  writeLines(medline_text, tmp, useBytes = TRUE)
  on.exit(unlink(tmp), add = TRUE)

  M <- tryCatch(
    import_corpus(tmp, dbsource = "pubmed", format = "pubmed"),
    error = function(e) {
      warning(sprintf("PubMed convert2df 失败: %s", conditionMessage(e)))
      NULL
    }
  )
  if (is.null(M) || !nrow(M)) return(NULL)
  .pubmed_add_id_cols(M)
}

# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------

#' 解析 PubMed .nbib 文件 (MEDLINE 格式) → bibliometrix corpus
#'
#' 直接复用 bibliometrix::convert2df(dbsource="pubmed", format="pubmed"),
#' 与上传 WoS/Scopus 走同一条解析管线, 字段一致. 解析后补 spec §4.4 的 PM/UT 列.
#'
#' @param path character(1); .nbib 文件路径
#' @return data.frame (bibliometrix corpus 格式) 或 NULL (文件缺失/解析失败)
nbib_parse <- function(path) {
  if (is.null(path) || length(path) != 1L || !nzchar(path) || !file.exists(path)) {
    warning("nbib_parse: 文件路径无效或不存在")
    return(NULL)
  }
  M <- tryCatch(
    import_corpus(path, dbsource = "pubmed", format = "pubmed"),
    error = function(e) {
      warning(sprintf(".nbib 解析失败: %s", conditionMessage(e)))
      NULL
    }
  )
  if (is.null(M) || !nrow(M)) return(NULL)
  .pubmed_add_id_cols(M)
}

#' 从 PubMed E-utilities 抓取并转为 bibliometrix corpus
#'
#' 三种入口由 query 形态决定:
#'   · 纯数字向量 / 逗号换行分隔的 PMID 串 → 跳过 esearch, 直接 efetch
#'   · 检索式 (含非数字字符) → 先 esearch 拿 PMID 列表, 再 efetch
#' efetch 返回 XML 后由 .parse_efetch_xml 还原 MEDLINE 并交给 bibliometrix.
#'
#' @param query character; PubMed 查询字符串, 或 PMID 向量 (纯数字)
#' @param max_records integer; 单次最大拉取条数 (默认 200L, NCBI 推荐)
#' @param api_key character; NCBI API key (可选, 提高限流到 ~10 req/s)
#' @return data.frame (bibliometrix corpus 格式) 或 NULL (空输入/网络/解析失败)
pubmed_to_corpus <- function(query, max_records = 200L, api_key = NULL) {
  # 空输入防御
  if (is.null(query) || length(query) == 0L ||
      all(!nzchar(trimws(as.character(query))))) {
    warning("pubmed_to_corpus: query 为空")
    return(NULL)
  }
  if (!is.null(api_key) && !nzchar(api_key)) api_key <- NULL
  max_records <- as.integer(max_records)
  if (is.na(max_records) || max_records < 1L) max_records <- 200L

  # 1. 决定 PMID 列表: 纯 PMID 直接用; 否则 esearch
  pmids <- .pubmed_as_pmids(query)
  if (is.null(pmids)) {
    pmids <- .pubmed_esearch(paste(query, collapse = " "),
                             max_records = max_records, api_key = api_key)
  }
  if (!length(pmids)) {
    warning("pubmed_to_corpus: 无匹配 PMID")
    return(NULL)
  }
  # 截断到 max_records
  if (length(pmids) > max_records) pmids <- pmids[seq_len(max_records)]

  # 2. efetch 拿 XML
  xml_text <- .pubmed_efetch(pmids, api_key = api_key)
  if (!nzchar(xml_text)) {
    warning("pubmed_to_corpus: efetch 无内容")
    return(NULL)
  }

  # 3. 解析为 corpus
  .parse_efetch_xml(xml_text)
}

# %||%: NULL/空 → 兜底 (与 fct_crossref.R 等同款语义)
`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
