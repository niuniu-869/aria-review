# R/fct_prisma.R — PRISMA 2020 流程图 (纯函数, ggplot 实现)
#
# v0.6 (spec §5) 设计决议:
#   · spec §5.5 原计划用 PRISMA2020 R 包 / DiagrammeR, 但二者均未装进 renv 库;
#     DiagrammeR 走 htmlwidgets, 静态 PNG/SVG 导出还需 DiagrammeRsvg+rsvg (也未装).
#   · 改用 ggplot2 (核心依赖, 必装) 画流程图: geom_rect 画框 + geom_text 写字 +
#     geom_segment 画箭头. 优点: 零新依赖, ggsave 可任意 DPI 导出 PNG/SVG/PDF
#     (满足 spec §5.4 的 SCI 投稿 300dpi 要求), 绘图函数可被 F2 报告复用.
#   · 简化为 spec §5.2 的五段计数: 识别 → 去重 → 筛选 → 排除(含理由) → 纳入.

#' PRISMA 五段计数的默认值
prisma_counts_default <- function() {
  list(
    identified = 0L,   # 识别: 检索获得的记录数 (去重前)
    duplicates = 0L,   # 移除的重复记录数
    screened   = 0L,   # 筛选: 去重后进入筛选的记录数
    excluded   = 0L,   # 排除: 筛选中被排除的记录数
    included   = 0L    # 纳入: 最终纳入的研究数
  )
}

#' 从 shared / corpus 尽力推导 PRISMA 计数 (best-effort 自动填充)
#'
#' 优先用 shared$prisma_autofill (由 mod_upload 去重步骤写入); 否则用 corpus 行数.
#' 用户始终可在 UI 手动覆盖.
#'
#' @param shared shiny::reactiveValues 或 list (可含 prisma_autofill / screen_passed_dois)
#' @param corpus data.frame 或 NULL
#' @return list 同 prisma_counts_default 结构
prisma_autofill <- function(shared = NULL, corpus = NULL) {
  cnt <- prisma_counts_default()

  # 1. 优先取 mod_upload 去重时写入的快照
  af <- tryCatch(shared$prisma_autofill, error = function(e) NULL)
  if (!is.null(af) && is.list(af)) {
    for (k in names(cnt)) if (!is.null(af[[k]])) cnt[[k]] <- as.integer(af[[k]])
  }

  # 2. corpus 行数兜底 (当前 corpus = 去重并纳入后的语料)
  n_corpus <- tryCatch(nrow(corpus), error = function(e) NULL)
  if (!is.null(n_corpus) && n_corpus > 0L) {
    if (cnt$screened == 0L)   cnt$screened   <- as.integer(n_corpus)
    if (cnt$identified == 0L) cnt$identified <- as.integer(n_corpus + cnt$duplicates)
    if (cnt$included == 0L)   cnt$included   <- as.integer(n_corpus - cnt$excluded)
  }

  # 3. 筛选模块的通过数 (若有)
  passed <- tryCatch(shared$screen_passed_dois, error = function(e) NULL)
  if (!is.null(passed) && length(passed) > 0L && cnt$included == 0L) {
    cnt$included <- as.integer(length(passed))
    if (cnt$screened >= cnt$included)
      cnt$excluded <- as.integer(cnt$screened - cnt$included)
  }

  cnt
}

#' 校验并规整 PRISMA 计数 (保证非负整数, 逻辑自洽提示)
#' @return list(counts, warnings) warnings 为中文一致性提示向量
prisma_validate <- function(counts) {
  cnt <- prisma_counts_default()
  for (k in names(cnt)) {
    v <- suppressWarnings(as.integer(counts[[k]] %||% 0L))
    cnt[[k]] <- if (is.na(v) || v < 0L) 0L else v
  }
  w <- character(0)
  if (cnt$screened != cnt$identified - cnt$duplicates)
    w <- c(w, "提示: 筛选数 ≠ 识别数 - 去重数, 请核对.")
  if (cnt$included != cnt$screened - cnt$excluded)
    w <- c(w, "提示: 纳入数 ≠ 筛选数 - 排除数, 请核对.")
  list(counts = cnt, warnings = w)
}

#' 生成 PRISMA 2020 流程图 (ggplot 对象)
#'
#' @param counts list (identified/duplicates/screened/excluded/included)
#' @param reasons character; 排除理由 (多行文本, 每行一条)
#' @param title   图标题
#' @return ggplot 对象 (可 print 渲染, 可 ggsave 导出)
prisma_flow_plot <- function(counts, reasons = "", title = "PRISMA 2020 流程图") {
  v <- prisma_validate(counts)
  cnt <- v$counts

  reason_txt <- ""
  if (nzchar(trimws(reasons %||% ""))) {
    lines <- trimws(strsplit(reasons, "\n", fixed = TRUE)[[1]])
    lines <- lines[nzchar(lines)]
    if (length(lines)) reason_txt <- paste0("\n", paste(paste0("· ", lines), collapse = "\n"))
  }

  # 主流程框 (居中竖列) 坐标
  main <- data.frame(
    x = 2,
    y = c(4, 3, 2, 1),
    label = c(
      sprintf("识别 Identification\n检索获得记录\nn = %d", cnt$identified),
      sprintf("去重 Deduplication\n去重后记录\nn = %d", cnt$screened),
      sprintf("筛选 Screening\n经筛选记录\nn = %d", cnt$screened),
      sprintf("纳入 Included\n最终纳入研究\nn = %d", cnt$included)
    ),
    stringsAsFactors = FALSE
  )
  # 右侧旁支框 (移除/排除)
  side <- data.frame(
    x = 4.2,
    y = c(3.5, 2),
    label = c(
      sprintf("移除重复记录\nn = %d", cnt$duplicates),
      sprintf("排除记录\nn = %d%s", cnt$excluded, reason_txt)
    ),
    stringsAsFactors = FALSE
  )

  box_w <- 1.6; box_h <- 0.7
  rect_df <- function(d, w, h) data.frame(
    xmin = d$x - w/2, xmax = d$x + w/2,
    ymin = d$y - h/2, ymax = d$y + h/2,
    label = d$label, x = d$x, y = d$y, stringsAsFactors = FALSE)
  m <- rect_df(main, box_w, box_h)
  s <- rect_df(side, box_w, box_h + 0.3)

  ggplot2::ggplot() +
    # 主框
    ggplot2::geom_rect(data = m,
      ggplot2::aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax),
      fill = "#eaf3fb", color = "#3c8dbc", linewidth = 0.6) +
    ggplot2::geom_text(data = m, ggplot2::aes(x = x, y = y, label = label),
      size = 3.1, lineheight = 0.95, color = "#1a3a4a") +
    # 旁支框
    ggplot2::geom_rect(data = s,
      ggplot2::aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax),
      fill = "#fdf3f3", color = "#c0392b", linewidth = 0.5) +
    ggplot2::geom_text(data = s, ggplot2::aes(x = x, y = y, label = label),
      size = 2.9, lineheight = 0.95, color = "#7a2a20") +
    # 竖向主箭头 (4→3→2→1)
    ggplot2::geom_segment(
      data = data.frame(x = 2, xend = 2,
                        y    = c(4, 3, 2) - box_h/2,
                        yend = c(3, 2, 1) + box_h/2),
      ggplot2::aes(x = x, xend = xend, y = y, yend = yend),
      arrow = ggplot2::arrow(length = ggplot2::unit(0.18, "cm"), type = "closed"),
      color = "#3c8dbc", linewidth = 0.6) +
    # 横向旁支箭头
    ggplot2::geom_segment(
      data = data.frame(x = 2 + box_w/2, xend = 4.2 - box_w/2,
                        y = c(3.5, 2), yend = c(3.5, 2)),
      ggplot2::aes(x = x, xend = xend, y = y, yend = yend),
      arrow = ggplot2::arrow(length = ggplot2::unit(0.15, "cm"), type = "closed"),
      color = "#c0392b", linewidth = 0.5) +
    ggplot2::labs(title = title) +
    ggplot2::coord_cartesian(xlim = c(0.8, 5.4), ylim = c(0.4, 4.6)) +
    ggplot2::theme_void(base_family = "") +
    ggplot2::theme(
      plot.title = ggplot2::element_text(hjust = 0.5, size = 13, face = "bold",
                                         margin = ggplot2::margin(b = 6)),
      plot.margin = ggplot2::margin(10, 10, 10, 10))
}

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0L) b else a
