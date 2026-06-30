# fct_llm_parse_refs.R — 非结构化文献文本 → bibliometrix corpus (路径 B)
#
# 战略意义: 学生导师丢给一份"参考文献清单"或者复制了一段 Google Scholar
# 输出, 我们用 DeepSeek 解析成结构化元数据 → 再用 OpenAlex 反查补全所有
# 字段 (摘要/引用/期刊/机构) → 组装成完整 corpus.
#
# 设计原则:
#   · LLM 只做"从乱文本里抽题录", 不做摘要/分析 (避免幻觉污染数据)
#   · OpenAlex 反查必须可靠, 标题相似度 ≥ 0.7 才接受匹配
#   · 匹配失败的条目要明确告诉用户哪些被丢弃, 不静默吞
#
# 调用方: mod_welcome.R 路径 B 卡片 / mod_upload.R 兜底入口

#' 用于 DeepSeek 的系统提示词 (中英混合; 学生大概率粘中英混杂文本)
.PARSE_REFS_SYSTEM_PROMPT <- "你是一个学术文献元数据抽取助手.

用户会粘贴一段非结构化文本, 可能是:
- Google Scholar 复制结果
- APA/MLA/Chicago 格式的参考文献清单
- 几段论文摘要或简介
- 期刊文章的标题列表
- 论文 PDF 第一页的文字

你的任务: 从中提取所有论文条目, 输出 JSON.

输出格式严格如下 (只输出 JSON, 不要任何额外文字):
{\"papers\": [
  {\"title\": \"...\", \"authors\": [\"...\", ...], \"year\": 2024, \"journal\": \"...\", \"doi\": \"...\"},
  ...
]}

字段约束:
- title: 论文标题原文 (英文优先), 必填
- authors: 作者姓名字符串数组, 形如 [\"Smith J\", \"Doe A\"], 缺失时给 []
- year: 4 位整数, 缺失时给 null
- journal: 期刊或会议名, 缺失时给 null
- doi: DOI 字符串 (不含 https://doi.org/ 前缀), 缺失时给 null

注意:
- 如果输入只有摘要而没有元数据, 跳过该条 (不要凭空编)
- 同一篇论文不要重复
- 文本里没有任何论文时, 返回 {\"papers\": []}"

#' 调 DeepSeek 把非结构化文本解析为结构化论文列表
#'
#' @param text 用户粘贴的原始文本
#' @return list of list(title, authors, year, journal, doi); 失败时 list()
.llm_extract_papers <- function(text, api_key = NULL) {
  if (!nzchar(text)) return(list())
  # DeepSeek JSON 模式必须在提示里强调 "JSON" 关键词 (官方约定)
  user_msg <- paste0("请提取以下文本里的论文条目, 严格按 JSON 格式返回:\n\n---\n",
                     text, "\n---")
  resp <- tryCatch(
    llm_call("deepseek",
             messages = list(
               list(role = "system", content = .PARSE_REFS_SYSTEM_PROMPT),
               list(role = "user",   content = user_msg)
             ),
             json_mode = TRUE,
             max_tokens = 4096L,
             temperature = 0.1,
             api_key = api_key),  # 抽取任务用低温稳定
    error = function(e) {
      warning(sprintf("DeepSeek 解析失败: %s", safe_log_error(e)))
      NULL
    })
  if (is.null(resp)) return(list())
  parsed <- tryCatch(jsonlite::fromJSON(resp$text, simplifyVector = FALSE),
                     error = function(e) {
                       warning(sprintf("LLM 输出非合法 JSON: %s",
                                       substr(resp$text, 1, 200)))
                       NULL
                     })
  if (is.null(parsed)) return(list())
  papers <- parsed$papers %||% list()
  # 基础校验: title 必填
  Filter(function(p) is.character(p$title) && nzchar(p$title), papers)
}

#' 对单个 paper 元数据, 在 OpenAlex 里反查 work 对象
#'
#' 策略:
#'   1. DOI 精确查 (如有) — 但**结果必须与 paper$title 相似度 ≥ 0.5**,
#'      否则视为 LLM 给的 DOI 错指, 降级到标题搜索. 实战踩到 LLM 给的
#'      DOI 在 OpenAlex 解析到完全不相关的论文.
#'   2. 标题模糊 — 相似度 ≥ 0.7 才接受.
#' @return list(work, matched_by) 或 NULL
.llm_resolve_one <- function(paper) {
  title <- paper$title %||% ""
  # 路 1: DOI 精确 + 标题交叉校验
  if (nzchar(paper$doi %||% "")) {
    w <- .oa_get_work_by_doi(paper$doi)
    if (!is.null(w)) {
      # DOI 查到了, 但要验证 OpenAlex 返回的标题跟 LLM 给的 title 一致
      sim <- if (nzchar(title))
        .oa_title_sim(title, w$title %||% w$display_name %||% "") else 1
      if (sim >= 0.5) {
        return(list(work = w,
                     matched_by = sprintf("doi(title-sim=%.2f)", sim)))
      }
      # DOI 指向完全无关的论文 → 不信任 DOI, 走标题路径
    }
  }
  # 路 2: 标题模糊
  if (nzchar(title)) {
    hit <- .oa_search_by_title(title, sim_min = 0.7)
    if (!is.null(hit)) return(list(work = hit$work,
                                     matched_by = sprintf("title(sim=%.2f)", hit$sim)))
  }
  NULL
}

#' 主入口: 非结构化文本 → bibliometrix corpus
#'
#' 三阶段: LLM 抽题录 → OpenAlex 反查 → 组装 corpus.
#' 匹配失败的条目记录到 attr(M, "unmatched"), 调用方可以提示用户.
#'
#' @param text         用户粘贴的文本
#' @param with_refs    是否补全引用 (默认 TRUE, 与路径 A 一致)
#' @param on_progress  function(stage, done, total, msg)
#'
#' @return data.frame (bibliometrix M) 或 NULL; attr "unmatched" 列出未匹配项
parse_refs_to_corpus <- function(text, with_refs = TRUE,
                                   on_progress = function(...) NULL,
                                   api_key = NULL) {
  if (!nzchar(text)) {
    warning("输入文本为空")
    return(NULL)
  }
  # 阶段 1: LLM 抽取
  on_progress(stage = "llm", done = 0, total = 1,
              msg = "AI 正在解析您粘贴的文本...")
  papers <- .llm_extract_papers(text, api_key = api_key)
  on_progress(stage = "llm", done = 1, total = 1,
              msg = sprintf("AI 识别出 %d 条论文条目", length(papers)))
  if (!length(papers)) {
    warning("未能从文本中识别出任何论文条目, 请检查输入内容")
    return(NULL)
  }

  # 阶段 2: OpenAlex 反查
  on_progress(stage = "match", done = 0, total = length(papers),
              msg = "在 OpenAlex 中匹配...")
  works     <- list()
  unmatched <- list()
  for (i in seq_along(papers)) {
    hit <- .llm_resolve_one(papers[[i]])
    if (!is.null(hit)) {
      works[[length(works) + 1L]] <- hit$work
    } else {
      unmatched[[length(unmatched) + 1L]] <- list(
        title = papers[[i]]$title,
        reason = "OpenAlex 未找到匹配 (DOI 不存在 且 标题相似度 < 0.7)"
      )
    }
    on_progress(stage = "match", done = i, total = length(papers),
                msg = sprintf("已匹配 %d/%d (跳过 %d)",
                               i - length(unmatched), i, length(unmatched)))
  }
  if (!length(works)) {
    warning(sprintf("识别出 %d 条但全部未在 OpenAlex 找到匹配", length(papers)))
    return(NULL)
  }

  # 阶段 3: 组装 corpus (复用路径 A 末端)
  M <- oa_corpus_from_works(works, with_refs = with_refs,
                              on_progress = on_progress)
  if (!is.null(M) && length(unmatched)) {
    attr(M, "unmatched") <- unmatched
  }
  M
}
