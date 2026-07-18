# services/r-analysis/plumber.R — 内部分析服务 (agent 经 httpx 调用)
# HTTP 层保持薄: 仅做请求解包 + 状态码, 业务逻辑全在 R/analysis.R 与 R/store.R
# (可被 testthat 直接测, 不依赖 plumber 运行时)。

library(bibliometrix)
library(jsonlite)
source("R/analysis.R")
source("R/store.R")
source("R/analysis_pages.R")
source("R/analysis_advanced.R")
source("R/analysis_advanced2.R")
source("R/analysis_networks.R")
source("R/cite.R")
source("R/ingest_openalex.R")
source("R/ingest_records.R")

# 分析端点共用: 加载语料 + 状态机 gating + 注入 corpusId (DRY)
.analysis_endpoint <- function(corpus_id, res, fn) {
  meta <- load_corpus_meta(corpus_id)
  if (is.null(meta)) {
    res$status <- 404
    return(list(code = "CORPUS_NOT_FOUND", message = "语料不存在"))
  }
  if (identical(meta$status, "parsing")) {
    res$status <- 409
    return(list(code = "CORPUS_NOT_READY", message = "语料解析中"))
  }
  if (identical(meta$status, "failed")) {
    res$status <- 422
    return(list(code = "PARSE_FAILED", message = meta$error %||% "解析失败"))
  }
  M <- load_corpus(corpus_id)
  if (is.null(M)) {
    res$status <- 404
    return(list(code = "CORPUS_NOT_FOUND", message = "语料数据缺失"))
  }
  tryCatch({
    out <- fn(M)
    out$corpusId <- corpus_id
    out
  }, error = function(e) {
    # 数据质量类失败 (DATA_QUALITY| 前缀, 用户可自救) → 422;
    # 其余真异常 → 502。502 只留给"服务真坏了", 监控才不误报。
    msg <- substr(conditionMessage(e), 1, 300)
    if (startsWith(msg, "DATA_QUALITY|")) {
      res$status <- 422
      dq_msg <- sub("^DATA_QUALITY\\|", "", msg)
      return(list(code = "ANALYSIS_FAILED",
                  message = if (nzchar(dq_msg)) paste0("分析失败：", dq_msg) else "分析失败"))
    }
    res$status <- 502
    # 透传具体错误(截断)：空洞的"分析失败"让用户无从自救(生产 QA 实例: 全空 PY)
    list(code = "ANALYSIS_FAILED",
         message = if (nzchar(msg)) paste0("分析失败：", msg) else "分析失败")
  })
}

# A4 高级图端点共用: 同 .analysis_endpoint 的语料 gating, 但 fn(M) 返回的是
# 可用性信封 {available, ...}; 一律返回 200 + 信封 (available:FALSE 也是 200, 非 502),
# 仅注入 corpusId / schemaVersion。语料级前置错误 (404/409/422) 仍走状态码。
.envelope_endpoint <- function(corpus_id, res, fn) {
  meta <- load_corpus_meta(corpus_id)
  if (is.null(meta)) {
    res$status <- 404
    return(list(code = "CORPUS_NOT_FOUND", message = "语料不存在"))
  }
  if (identical(meta$status, "parsing")) {
    res$status <- 409
    return(list(code = "CORPUS_NOT_READY", message = "语料解析中"))
  }
  if (identical(meta$status, "failed")) {
    res$status <- 422
    return(list(code = "PARSE_FAILED", message = meta$error %||% "解析失败"))
  }
  M <- load_corpus(corpus_id)
  if (is.null(M)) {
    res$status <- 404
    return(list(code = "CORPUS_NOT_FOUND", message = "语料数据缺失"))
  }
  # fn 内部已 tryCatch (analysis_envelope), 不应抛错; 万一抛 → 信封化 analysis_error。
  env <- tryCatch(fn(M), error = function(e)
    list(available = FALSE, reason = "analysis_error",
         message = "分析计算出错, 已捕获。", detail = conditionMessage(e)))
  env$schemaVersion <- 1L
  env$corpusId <- corpus_id
  env
}

#* @apiTitle BiblioCN r-analysis (internal)
#* @apiDescription bibliometrix 分析内核, 仅供 agent 后端内部调用

#* 健康探针
#* @get /healthz
#* @serializer unboxedJSON
function() {
  list(status = "ok", service = "r-analysis")
}

#* 解析上传文件为语料 (状态机: ready/failed)
#* @post /parse
#* @parser multi
#* @serializer unboxedJSON
function(req, res) {
  # multipart 字段从 req$body 直接读, 不靠签名自动绑定 (plumber 会传 length-0)
  dbsource <- .form_text(req$body$dbsource, "wos")
  if (!dbsource %in% c("wos", "scopus")) {
    res$status <- 400
    return(list(code = "VALIDATION_ERROR", message = "dbsource 仅支持 wos/scopus"))
  }
  fileobj <- req$body$file
  if (is.null(fileobj)) {
    res$status <- 400
    return(list(code = "VALIDATION_ERROR", message = "缺少 file 字段"))
  }
  # 防御: parser 可能给 raw / list(value=raw) / list(datapath=路径)
  raw <- NULL; path <- NULL
  if (is.list(fileobj)) {
    if (!is.null(fileobj$value)) raw <- fileobj$value
    else if (!is.null(fileobj$datapath)) path <- fileobj$datapath
  } else {
    raw <- fileobj
  }
  if (is.raw(raw) && length(raw) > 50 * 1024^2) {   # 50MB 上限 (Codex step2-P2)
    res$status <- 413
    return(list(code = "PAYLOAD_TOO_LARGE", message = "文件超过 50MB 上限"))
  }
  tmp <- tempfile(fileext = ".txt")
  on.exit(unlink(tmp), add = TRUE)
  if (!is.null(path) && file.exists(path)) {
    file.copy(path, tmp, overwrite = TRUE)
  } else if (is.raw(raw)) {
    writeBin(raw, tmp)
  } else {
    writeLines(as.character(raw), tmp)
  }

  meta <- parse_and_store(tmp, dbsource)
  # 同步解析: ready 返回 200 (Codex step2-P1; 真正 async/202 由 agent 负责)
  res$status <- if (identical(meta$status, "failed")) 422L else 200L
  meta
}

#* 查询语料状态 (前端 gating)
#* @get /corpus/<corpus_id>
#* @serializer unboxedJSON
function(corpus_id, res) {
  meta <- load_corpus_meta(corpus_id)
  if (is.null(meta)) {
    res$status <- 404
    return(list(code = "CORPUS_NOT_FOUND", message = "语料不存在"))
  }
  meta
}

#* 领域概览分析
#* @get /corpus/<corpus_id>/overview
#* @serializer unboxedJSON
function(corpus_id, res) {
  .analysis_endpoint(corpus_id, res, overview_dto)
}

#* 来源分析 (核心期刊 / h 指数 / Bradford 分区)
#* @get /corpus/<corpus_id>/sources
#* @serializer unboxedJSON list(null="null")
function(corpus_id, res) {
  .analysis_endpoint(corpus_id, res, sources_dto)
}

#* 作者分析 (高产作者 / h 指数 / Lotka 定律)
#* @get /corpus/<corpus_id>/authors
#* @serializer unboxedJSON list(null="null")
function(corpus_id, res) {
  .analysis_endpoint(corpus_id, res, authors_dto)
}

#* 文档与关键词 (高被引文献 / 关键词词频)
#* @get /corpus/<corpus_id>/documents
#* @serializer unboxedJSON
function(corpus_id, res) {
  .analysis_endpoint(corpus_id, res, documents_dto)
}

#* 作者年度产出时间线 (热力图: 作者 × 年份) — A4, 返回可用性信封
#* @get /corpus/<corpus_id>/authors/production
#* @serializer unboxedJSON
function(corpus_id, res) {
  .envelope_endpoint(corpus_id, res, author_production_dto)
}

#* 关键词历时演变 (themeRiver / 堆叠面积) — A4, 返回可用性信封
#* @get /corpus/<corpus_id>/documents/keyword-trend
#* @serializer unboxedJSON
function(corpus_id, res) {
  .envelope_endpoint(corpus_id, res, keyword_trend_dto)
}

#* 高被引参考文献 (参考文献 | 次数) — A4, 返回可用性信封
#* @get /corpus/<corpus_id>/documents/cited-refs
#* @serializer unboxedJSON
function(corpus_id, res) {
  .envelope_endpoint(corpus_id, res, cited_refs_dto)
}

#* 主题战略图 (Callon 中心度×密度 四象限) — A5, 返回可用性信封
#* @get /corpus/<corpus_id>/conceptual/thematic
#* @serializer unboxedJSON
function(corpus_id, res) {
  .envelope_endpoint(corpus_id, res, thematic_dto)
}

#* 主题演进图 (多周期主题流 / Sankey) — A5, 返回可用性信封
#* @get /corpus/<corpus_id>/conceptual/evolution
#* @serializer unboxedJSON
function(corpus_id, res) {
  .envelope_endpoint(corpus_id, res, evolution_dto)
}

#* 历史引文图 (时序分层引用脉络) — A5, 返回可用性信封
#* @get /corpus/<corpus_id>/intellectual/histcite
#* @serializer unboxedJSON
function(corpus_id, res) {
  .envelope_endpoint(corpus_id, res, histcite_dto)
}

#* 三字段 Sankey (作者→关键词→来源) — A5, 返回可用性信封
#* @get /corpus/<corpus_id>/overview/threefield
#* @serializer unboxedJSON
function(corpus_id, res) {
  .envelope_endpoint(corpus_id, res, threefield_dto)
}

# 网络端点 limit 钳制 .net_limit() 定义于 R/analysis_networks.R (已 source), 便于 testthat 覆盖。

#* 概念结构 (关键词共现网络) — A5: ?limit 默认 top100 (前端滑块切片)
#* @get /corpus/<corpus_id>/conceptual
#* @serializer unboxedJSON
function(corpus_id, res, limit = 100) {
  n <- .net_limit(limit)
  .analysis_endpoint(corpus_id, res, function(M) conceptual_dto(M, n = n))
}

#* 知识结构 (参考文献共被引网络) — A5: ?limit 默认 top100
#* @get /corpus/<corpus_id>/intellectual
#* @serializer unboxedJSON
function(corpus_id, res, limit = 100) {
  n <- .net_limit(limit)
  .analysis_endpoint(corpus_id, res, function(M) intellectual_dto(M, n = n))
}

#* 社会结构 (作者/国家合作网络) — A5: ?limit 默认 top100
#* @get /corpus/<corpus_id>/social
#* @serializer unboxedJSON
function(corpus_id, res, limit = 100) {
  n <- .net_limit(limit)
  .analysis_endpoint(corpus_id, res, function(M) social_dto(M, n = n))
}

#* 引用导出 (GB/T 7714 / APA / MLA)
#* @get /corpus/<corpus_id>/cite
#* @serializer unboxedJSON
function(corpus_id, res, style = "apa", limit = 200) {
  st <- if (style %in% c("gbt7714", "apa", "mla")) style else "apa"
  lim <- suppressWarnings(as.integer(limit))
  if (is.na(lim) || lim < 1L) lim <- 200L
  .analysis_endpoint(corpus_id, res, function(M) {
    list(schemaVersion = 1L, style = st, citations = as.list(corpus_citations(M, st, lim)))
  })
}

#* 主题词只检索 (路径 D): OpenAlex → 规范化候选列表, 不建库
#* body: {"query":"...", "n":25, "since":"2016-01-01"}；limit 可作为 n 的别名
#* 入参校验: query 必须是 scalar string; n/limit 必须是 scalar int; since 必须是 YYYY 或 YYYY-MM-DD
#* n/limit 上限 500；超过显式 400，不静默截断
#* 错误语义: 400=入参非法; 502=首轮 OpenAlex/网络故障; 200={results:[]}=真空或部分结果
#* @post /search/openalex
#* @serializer unboxedJSON
function(req, res) {
  body <- tryCatch(
    jsonlite::fromJSON(req$postBody, simplifyVector = FALSE),
    error = function(e) list()
  )

  # --- P2-1: 显式 scalar 入参校验 (业务逻辑之前, 400 优先) ---
  raw_query <- body$query
  if (!is.character(raw_query) || length(raw_query) != 1L || !nzchar(trimws(raw_query))) {
    res$status <- 400
    return(list(error = "VALIDATION_ERROR",
                detail = "query must be a non-empty scalar string"))
  }
  query <- trimws(raw_query)

  raw_n <- if (!is.null(body$n)) body$n else body$limit
  if (is.null(raw_n)) {
    n <- 25L
  } else if (!is.numeric(raw_n) || length(raw_n) != 1L) {
    res$status <- 400
    return(list(error = "VALIDATION_ERROR",
                detail = "n/limit must be a scalar integer"))
  } else {
    if (raw_n %% 1 != 0) {
      res$status <- 400
      return(list(error = "VALIDATION_ERROR",
                  detail = "n/limit must be an integer value (no fractional part)"))
    }
    n <- suppressWarnings(as.integer(raw_n))
    if (is.na(n) || n < 1L) {
      res$status <- 400
      return(list(error = "VALIDATION_ERROR",
                  detail = "n/limit must be a positive integer"))
    }
    if (n > 500L) {
      res$status <- 400
      return(list(error = "VALIDATION_ERROR",
                  detail = "n/limit must be <= 500"))
    }
  }

  raw_since <- body$since
  if (is.null(raw_since)) {
    since <- "2016-01-01"
  } else if (!is.character(raw_since) || length(raw_since) != 1L) {
    res$status <- 400
    return(list(error = "VALIDATION_ERROR",
                detail = "since must be a scalar string (YYYY or YYYY-MM-DD)"))
  } else {
    since <- trimws(raw_since)
    if (!grepl("^[0-9]{4}(-[0-9]{2}-[0-9]{2})?$", since)) {
      res$status <- 400
      return(list(error = "VALIDATION_ERROR",
                  detail = "since must be YYYY or YYYY-MM-DD format"))
    }
    # 纯 YYYY 补完为 YYYY-01-01
    if (nchar(since) == 4L) {
      since <- paste0(since, "-01-01")
    } else {
      # YYYY-MM-DD：校验是否为真实合法日期（如 2024-13-99 应拒绝）
      parsed <- tryCatch(as.Date(since, format = "%Y-%m-%d"), error = function(e) NA_Date_)
      if (is.na(parsed)) {
        res$status <- 400
        return(list(error = "VALIDATION_ERROR",
                    detail = paste0("since '", since, "' is not a valid calendar date")))
      }
    }
  }

  # --- 业务逻辑 ---
  tryCatch({
    candidates <- oa_search_candidates(query, n, since)
    out <- list(results = candidates)
    if (.oa_partial(candidates)) {
      out$partial <- TRUE
      out$partialReason <- .oa_partial_reason(candidates)
    }
    out
  }, error = function(e) {
    msg <- conditionMessage(e)
    if (startsWith(msg, "OPENALEX_LIMIT_EXCEEDED|")) {
      parts <- strsplit(msg, "|", fixed = TRUE)[[1]]
      res$status <- 400
      return(list(error = "VALIDATION_ERROR",
                  detail = paste(parts[-1], collapse = "|")))
    }
    # oa_search_candidates 在 OpenAlex 不可达时抛 "OPENALEX_UNAVAILABLE|<status>|<msg>"
    if (startsWith(msg, "OPENALEX_UNAVAILABLE|")) {
      parts <- strsplit(msg, "|", fixed = TRUE)[[1]]
      http_status <- suppressWarnings(as.integer(parts[2]))
      detail_msg  <- paste(parts[-(1:2)], collapse = "|")
      res$status  <- 502
      return(list(error = "OPENALEX_UNAVAILABLE",
                  message = if (nzchar(detail_msg)) detail_msg
                            else "OpenAlex 服务不可达，请稍后重试",
                  status  = if (!is.na(http_status)) http_status else 0L))
    }
    # 其他未预期错误 → 502
    res$status <- 502
    list(error = "SEARCH_FAILED", message = "检索服务内部错误", detail = msg)
  })
}

#* 主题词检索建库 (路径 A): OpenAlex → WoS plaintext → convert2df → 存储
#* @post /corpus/from-topic
#* @serializer unboxedJSON
function(req, res, query = "", n = 50, since = "2016-01-01", withRefs = TRUE) {
  q <- trimws(if (is.character(query) && length(query)) query[1] else "")
  if (!nzchar(q)) {
    res$status <- 400
    return(list(code = "VALIDATION_ERROR", message = "缺少 query 主题词"))
  }
  nn <- suppressWarnings(as.integer(n)); if (is.na(nn) || nn < 1L) nn <- 50L
  nn <- min(200L, nn)
  wr <- !isFALSE(withRefs) && !identical(tolower(as.character(withRefs)[1]), "false")
  tryCatch({
    path <- oa_build_wos_from_topic(q, n = nn, since = since, with_refs = wr)
    if (is.null(path)) {
      res$status <- 422
      list(code = "NO_RESULTS", message = "OpenAlex 未检索到结果, 换个主题词或用英文试试")
    } else {
      on.exit(unlink(path), add = TRUE)
      meta <- parse_and_store(path, "wos")
      res$status <- if (identical(meta$status, "failed")) 422L else 200L
      meta
    }
  }, error = function(e) {
    res$status <- 502
    list(code = "INGEST_FAILED", message = "主题词建库失败")
  })
}

#* 参考文献建库 (路径 B): agent 已抽好的 papers → OpenAlex 反查 → WoS → 存储
#* @post /corpus/from-refs
#* @serializer unboxedJSON
function(req, res, withRefs = TRUE) {
  papers <- req$body$papers
  if (is.null(papers) || !length(papers)) {
    res$status <- 400
    return(list(code = "VALIDATION_ERROR", message = "缺少 papers"))
  }
  wr <- !isFALSE(withRefs) && !identical(tolower(as.character(withRefs)[1]), "false")
  tryCatch({
    built <- oa_build_wos_from_papers(papers, with_refs = wr)
    if (is.null(built$path)) {
      res$status <- 422
      list(code = "NO_RESULTS", message = "参考文献均未在 OpenAlex 匹配到",
           unmatched = length(built$unmatched))
    } else {
      on.exit(unlink(built$path), add = TRUE)
      meta <- parse_and_store(built$path, "wos")
      res$status <- if (identical(meta$status, "failed")) 422L else 200L
      meta$matched <- built$matched
      meta$unmatched <- length(built$unmatched)
      meta
    }
  }, error = function(e) {
    res$status <- 502
    list(code = "INGEST_FAILED", message = "参考文献建库失败")
  })
}

#* 结构化题录建库 (路径 C): Postgres included papers → bibliometrix 数据框 → 存储
#* 接收 JSON 数组 {"records": [...]}，每条记录含 title/authors/year/doi/abstract/
#* keywords/container_title/volume/issue/pages/creators/csl_json 等字段（贴合 paper 表）。
#* 直接映射为 bibliometrix 兼容数据框，不调用 OpenAlex，完全保真。
#* @post /parse-from-records
#* @serializer unboxedJSON
function(req, res) {
  records <- req$body$records
  if (is.null(records) || !length(records)) {
    res$status <- 400L
    return(list(code = "VALIDATION_ERROR", message = "缺少 records 字段或数组为空"))
  }
  tryCatch({
    meta <- parse_from_records_and_store(records)
    res$status <- if (identical(meta$status, "failed")) 422L else 200L
    meta
  }, error = function(e) {
    res$status <- 502L
    list(code = "INGEST_FAILED", message = "结构化题录建库失败")
  })
}

#* 语料文献列表 (供综述 grounding)
#* @get /corpus/<corpus_id>/records
#* @serializer unboxedJSON
function(corpus_id, res, limit = 50) {
  meta <- load_corpus_meta(corpus_id)
  if (is.null(meta)) {
    res$status <- 404
    return(list(code = "CORPUS_NOT_FOUND", message = "语料不存在"))
  }
  if (!identical(meta$status, "ready")) {
    res$status <- 409
    return(list(code = "CORPUS_NOT_READY", message = "语料未就绪"))
  }
  M <- load_corpus(corpus_id)
  if (is.null(M)) {
    res$status <- 404
    return(list(code = "CORPUS_NOT_FOUND", message = "语料数据缺失"))
  }
  lim <- suppressWarnings(as.integer(limit))
  if (is.na(lim) || lim < 1L) lim <- 50L
  list(corpusId = corpus_id, records = corpus_records(M, lim))
}
