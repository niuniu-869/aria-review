# services/r-analysis/R/store.R
# 语料存取 + 状态机 (Codex #5)。
# Phase 0 用 RDS 持久化 — 仅系统自己生成的产物 (Codex #18: 绝不 readRDS 用户上传)。
# parquet 迁移见设计 §12; RDS 绑 R 版本, 后续换 parquet + 明确 schema。
# 原子写: 写 .tmp 再 rename, 避免读到半截文件 (Codex #5 语料竞态)。

CORPORA_DIR <- function() {
  Sys.getenv("BIBLIOCN_CORPORA_DIR",
             unset = file.path(tempdir(), "bibliocn-corpora"))
}
.corpus_path <- function(id) file.path(CORPORA_DIR(), paste0(id, ".rds"))
.meta_path   <- function(id) file.path(CORPORA_DIR(), paste0(id, ".meta.json"))

# 仅接受 UUID v4 形 id, 防路径遍历 + 防 readRDS 不可信路径 (Codex step2-P1)
.is_valid_id <- function(id) {
  is.character(id) && length(id) == 1L && !is.na(id) &&
    grepl("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", id)
}

# UUID v4, 不依赖 uuid 包
new_corpus_id <- function() {
  hx <- function(n) paste(sprintf("%x", sample(0:15, n, replace = TRUE)), collapse = "")
  sprintf("%s-%s-4%s-%s%s-%s",
          hx(8), hx(4), hx(3), sample(c("8","9","a","b"), 1), hx(3), hx(12))
}

#' 原子保存语料 + 元数据, 返回 meta list
#' @param M data.frame 或 NULL (status=failed 时)
#' @param status "ready" | "failed" | "parsing"
save_corpus <- function(M, corpus_id, dbsource, status = "ready", error = NULL) {
  if (!.is_valid_id(corpus_id)) stop("save_corpus: 非法 corpus_id")
  dir.create(CORPORA_DIR(), recursive = TRUE, showWarnings = FALSE)
  if (!is.null(M)) {
    # 唯一 tmp + 检查 rename, 避免并发覆盖与静默失败 (Codex step2-P1)
    tmp <- tempfile(tmpdir = CORPORA_DIR(), fileext = ".rds.tmp")
    saveRDS(M, tmp)
    if (!file.rename(tmp, .corpus_path(corpus_id))) {
      unlink(tmp); stop("save_corpus: 语料文件重命名失败")
    }
  }
  meta <- list(
    corpusId      = corpus_id,
    dbsource      = dbsource,
    status        = status,
    documentCount = if (!is.null(M)) as.integer(nrow(M)) else NULL,
    error         = error,
    schemaVersion = 1L,
    createdAt     = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  )
  meta <- meta[!vapply(meta, is.null, logical(1))]
  tmpm <- tempfile(tmpdir = CORPORA_DIR(), fileext = ".meta.tmp")
  writeLines(jsonlite::toJSON(meta, auto_unbox = TRUE, null = "null"), tmpm)
  if (!file.rename(tmpm, .meta_path(corpus_id))) {
    unlink(tmpm); stop("save_corpus: 元数据重命名失败")
  }
  meta
}

load_corpus_meta <- function(corpus_id) {
  if (!.is_valid_id(corpus_id)) return(NULL)
  p <- .meta_path(corpus_id)
  if (!file.exists(p)) return(NULL)
  jsonlite::fromJSON(p, simplifyVector = TRUE)
}

load_corpus <- function(corpus_id) {
  if (!.is_valid_id(corpus_id)) return(NULL)
  p <- .corpus_path(corpus_id)
  if (!file.exists(p)) return(NULL)
  readRDS(p)
}

#' 解析上传文件 → 存储 → 返回 meta (状态机: ready/failed)
#' 错误脱敏 (Codex #18): 不把原始 R 错误外泄给客户端。
parse_and_store <- function(file_path, dbsource, corpus_id = new_corpus_id()) {
  fmt <- if (identical(dbsource, "scopus")) "csv" else "plaintext"
  tryCatch({
    M <- bibliometrix::convert2df(file = file_path, dbsource = dbsource, format = fmt)
    if (!is.data.frame(M) || nrow(M) == 0L) stop("解析得到空语料")
    save_corpus(M, corpus_id, dbsource, status = "ready")
  }, error = function(e) {
    save_corpus(NULL, corpus_id, dbsource, status = "failed",
                error = "解析失败: 文件格式或内容无法识别")
  })
}
