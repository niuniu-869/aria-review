# global.R — 加载包、中文字体、i18n 文案表
# 注: source(R/*.R) 之前先 load_env() 否则 fct_llm_deepseek 在 startup probe 时找不到 key

library(shiny)
library(bs4Dash)
library(bibliometrix)
library(DT)
library(plotly)
library(ggplot2)
library(visNetwork)
library(ggwordcloud)
library(showtext)
library(httr2)         # LLM 客户端
library(jsonlite)      # JSON 解析
library(processx)      # 异步子进程 (lit_pipeline.py)
library(later)         # poll stdout
library(promises)      # Shiny 异步
library(future)        # Shiny 异步
library(yaml)          # config.yml 解析
library(htmltools)     # prompt injection 转义
library(markdown)      # 综述 markdown → HTML 渲染

# 限制文件上传大小为 50MB，防止超大文件占用过多服务器内存
options(shiny.maxRequestSize = 50 * 1024^2)

# 中文字体：解决 ggplot/词云中文乱码
showtext_auto()

# 先加载 .env (源码层 fct_env.R 在下面 source 时才定义 load_env)
# 因此显式先 source fct_env, 再 load_env, 再 source 其余
source("R/fct_env.R")
load_env(".env")

# 加载纯函数层与模块层 (含 LLM 全套)
for (f in list.files("R", pattern = "\\.R$", full.names = TRUE)) {
  if (basename(f) != "fct_env.R") source(f)
}

# 异步执行计划: Shiny + future_promise 用多 session 后台 R 进程
future::plan(future::multisession, workers = max(2, parallel::detectCores() - 2))

# i18n 中文文案表 (集中管理, 便于后续维护)
#
# 文案策略 (2026-05 学生友好化重写):
#   · 主标题: 学生能听懂 — 写综述/写开题视角, 不用纯计量术语
#   · menu_*: 主名词 + 副标题"这页能回答..."(各模块 page_header 用)
#   · 行话第一次出现时给括号注释 (例: h 指数, Bradford 定律)
LBL <- list(
  app_title         = "BiblioCN  文献综述助手",
  # 入口/数据
  menu_welcome      = "首页",
  menu_upload       = "数据导入",
  # 文献计量分析 (8 项)
  menu_overview     = "领域概览",
  menu_sources      = "核心期刊",
  menu_authors      = "核心作者",
  menu_documents    = "关键词与热点",
  menu_conceptual   = "研究主题地图",
  menu_intellectual = "学科知识脉络",
  menu_social       = "合作关系网",
  # v0.6 新增
  menu_prisma       = "PRISMA 流程图",
  menu_report       = "导出报告",
  # AI 助手
  menu_ai           = "AI 助手",
  menu_pdf_fetch    = "PDF 全文获取",
  menu_ai_screen    = "相关性筛选",
  menu_ai_translate = "文献翻译",
  menu_ai_summary   = "文献总结",
  menu_ai_review    = "综述写作",
  menu_ai_rewrite   = "交互重写",
  menu_ai_chat      = "与语料对话",
  menu_ai_cite      = "引用导出",
  menu_settings     = "设置",
  # 全局提示
  no_data           = "还没有数据? 回到首页选一条入门路径 (主题词搜索 / 演示数据 / 粘贴文献清单)。",
  privacy           = "提示: 本平台分析在服务器本地完成; AI 助手会将文献标题、摘要、用户输入主题发送到 DeepSeek 服务器。详见『设置』页。"
)
