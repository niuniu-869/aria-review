# app.R — BiblioCN 启动入口，bs4Dash 中文外壳

source("global.R")

ui <- bs4Dash::dashboardPage(
  title = LBL$app_title,
  header = bs4Dash::dashboardHeader(title = LBL$app_title),
  sidebar = bs4Dash::dashboardSidebar(
    # FINDING-015: sidebar 顶部加快速过滤输入框. BiblioCN 跨 17 个 tab,
    # 全部展开后视觉密度高; 过滤可以让用户在记得部分关键字时秒跳, 而不
    # 是逐项扫描. 实现: 纯 client-side JS, 监听 input 隐藏/显示 .nav-item.
    div(class = "biblio-sidebar-search",
        tags$input(type = "search", id = "biblio_sidebar_search",
                   class = "form-control form-control-sm",
                   placeholder = "搜索菜单… (按 Esc 清空)",
                   autocomplete = "off")),
    tags$script(HTML("
      $(function() {
        var input = document.getElementById('biblio_sidebar_search');
        if (!input) return;
        function textOf(el, deep) {
          // deep=false 时仅取直接 .nav-link 文本, 不包含 treeview 子菜单文本
          if (deep) return el.textContent.toLowerCase();
          var link = el.querySelector(':scope > .nav-link');
          return link ? link.textContent.toLowerCase() : el.textContent.toLowerCase();
        }
        function filter() {
          var q = input.value.trim().toLowerCase();
          var topItems = document.querySelectorAll('.main-sidebar .nav-sidebar > .nav-item');
          topItems.forEach(function(item) {
            var isTree = item.classList.contains('has-treeview');
            if (q === '') {
              // 清空: 显示所有顶层项, 显示所有子项, 不主动改 menu-open
              item.style.display = '';
              item.querySelectorAll(':scope .nav-treeview .nav-item').forEach(function(sub) {
                sub.style.display = '';
              });
              return;
            }
            var topMatch = textOf(item, false).indexOf(q) !== -1;
            if (!isTree) {
              item.style.display = topMatch ? '' : 'none';
              return;
            }
            // 嵌套父菜单: 子项命中也视作父命中, 同时只显示命中子项 + 展开父级
            var subItems = item.querySelectorAll(':scope .nav-treeview .nav-item');
            var anySubMatch = false;
            subItems.forEach(function(sub) {
              var match = sub.textContent.toLowerCase().indexOf(q) !== -1;
              sub.style.display = match ? '' : 'none';
              if (match) anySubMatch = true;
            });
            if (topMatch || anySubMatch) {
              item.style.display = '';
              if (anySubMatch) item.classList.add('menu-open');
            } else {
              item.style.display = 'none';
            }
          });
        }
        input.addEventListener('input', filter);
        input.addEventListener('keydown', function(e) {
          if (e.key === 'Escape') { input.value = ''; filter(); }
        });
      });
    ")),
    bs4Dash::sidebarMenu(
      id = "menu",
      # 欢迎页 — 新人零门槛入口 (路径 A/B/D/E 四张卡片), 默认登陆 tab.
      # 已有数据的用户可直接点上传或概览跳过.
      bs4Dash::menuItem(LBL$menu_welcome,      tabName = "welcome",      icon = icon("house"),
                        selected = TRUE),
      bs4Dash::menuItem(LBL$menu_upload,       tabName = "upload",       icon = icon("upload")),
      bs4Dash::menuItem(LBL$menu_overview,     tabName = "overview",     icon = icon("chart-pie")),
      bs4Dash::menuItem(LBL$menu_sources,      tabName = "sources",      icon = icon("book")),
      bs4Dash::menuItem(LBL$menu_authors,      tabName = "authors",      icon = icon("users")),
      bs4Dash::menuItem(LBL$menu_documents,    tabName = "documents",    icon = icon("file-lines")),
      bs4Dash::menuItem(LBL$menu_conceptual,   tabName = "conceptual",   icon = icon("diagram-project")),
      bs4Dash::menuItem(LBL$menu_intellectual, tabName = "intellectual", icon = icon("sitemap")),
      bs4Dash::menuItem(LBL$menu_social,       tabName = "social",       icon = icon("globe")),
      # v0.6: 系统综述工作流 (PRISMA) + 报告导出
      bs4Dash::menuItem(LBL$menu_prisma,       tabName = "prisma",       icon = icon("diagram-predecessor")),
      bs4Dash::menuItem(LBL$menu_report,       tabName = "report",       icon = icon("file-export")),
      # AI 助手 - 嵌套子菜单 (Phase 2 新增, 后续轮次逐项启用)
      bs4Dash::menuItem(LBL$menu_ai, icon = icon("robot"), startExpanded = FALSE,
        bs4Dash::menuSubItem(LBL$menu_ai_screen,    tabName = "ai_screen",    icon = icon("filter")),
        bs4Dash::menuSubItem(LBL$menu_pdf_fetch,    tabName = "pdf_fetch",    icon = icon("file-pdf")),
        bs4Dash::menuSubItem(LBL$menu_ai_translate, tabName = "ai_translate", icon = icon("language")),
        bs4Dash::menuSubItem(LBL$menu_ai_summary,   tabName = "ai_summary",   icon = icon("compress")),
        bs4Dash::menuSubItem(LBL$menu_ai_review,    tabName = "ai_review",    icon = icon("pen-fancy")),
        bs4Dash::menuSubItem(LBL$menu_ai_rewrite,   tabName = "ai_rewrite",   icon = icon("wand-magic-sparkles")),
        bs4Dash::menuSubItem(LBL$menu_ai_chat,      tabName = "ai_chat",      icon = icon("comments")),
        bs4Dash::menuSubItem(LBL$menu_ai_cite,      tabName = "ai_cite",      icon = icon("quote-right"))
      ),
      bs4Dash::menuItem(LBL$menu_settings, tabName = "settings", icon = icon("gear"))
      # R6: 设置 (Provider / Key / 费用看板)
    )
  ),
  body = bs4Dash::dashboardBody(
    # FINDING-001/006: 注入中文字体优先栈 + 卡片头淡化补丁
    tags$head(tags$link(rel = "stylesheet", type = "text/css", href = "biblio.css")),
    uiOutput("global_hint"),
    bs4Dash::tabItems(
      bs4Dash::tabItem("welcome",      welcomeUI("welcome")),
      bs4Dash::tabItem("upload",       uploadUI("upload")),
      bs4Dash::tabItem("overview",     overviewUI("overview")),
      bs4Dash::tabItem("sources",      sourcesUI("sources")),
      bs4Dash::tabItem("authors",      authorsUI("authors")),
      bs4Dash::tabItem("documents",    documentsUI("documents")),
      bs4Dash::tabItem("conceptual",   conceptualUI("conceptual")),
      bs4Dash::tabItem("intellectual", intellectualUI("intellectual")),
      bs4Dash::tabItem("social",       socialUI("social")),
      bs4Dash::tabItem("prisma",       prismaUI("prisma")),
      bs4Dash::tabItem("report",       reportUI("report")),
      bs4Dash::tabItem("ai_screen",    aiScreenUI("ai_screen")),
      bs4Dash::tabItem("pdf_fetch",    pdfFetchUI("pdf_fetch")),
      bs4Dash::tabItem("ai_translate", aiTranslateUI("ai_translate")),
      bs4Dash::tabItem("ai_summary",   aiSummaryUI("ai_summary")),
      bs4Dash::tabItem("ai_review",    aiReviewUI("ai_review")),
      bs4Dash::tabItem("ai_rewrite",   aiRewriteUI("ai_rewrite")),
      bs4Dash::tabItem("ai_chat",      aiChatUI("ai_chat")),
      bs4Dash::tabItem("ai_cite",      aiCiteUI("ai_cite")),
      bs4Dash::tabItem("settings",     settingsUI("settings"))
    )
  ),
  footer = bs4Dash::dashboardFooter(left = LBL$privacy)
)

server <- function(input, output, session) {
  # 共享 corpus reactiveVal — welcome 和 upload 两个入口都往这里写,
  # 下游模块统一从这里消费. mod_upload 仍然返回自己的 reactiveVal,
  # 我们做一个 observe 桥接, 把它的值反射到 corpus_rv 上, 避免改动
  # mod_upload 的接口.
  corpus_rv <- reactiveVal(NULL)

  # ── 跨模块共享状态 (codex 修订: 必须在 server 内创建, 不进 global) ──
  # 注: 提前到此处创建, 以便 uploadServer 写入 PRISMA 自动填充快照.
  shared <- reactiveValues(
    screen_passed_dois = character(0),
    pdf_corpus         = NULL,
    cost_log           = cost_log_empty(),
    # v0.6 跨模块状态:
    review_chapters    = list(),      # mod_ai_review 写入, mod_report 读取
    prisma_state       = NULL,        # mod_prisma 写入 (counts+reasons), mod_report 读取
    prisma_autofill    = NULL,        # mod_upload 去重后写入, mod_prisma 读取
    model              = "deepseek-v4-flash"
  )

  # Upload 模块: 保持原接口, 桥接到 corpus_rv; 传入 shared 写 PRISMA 自动填充
  upload_corpus <- uploadServer("upload", shared = shared)
  observe({
    val <- upload_corpus()
    if (!is.null(val)) corpus_rv(val)
  })

  # Welcome 模块: 直接写 corpus_rv, 并能跳 tab
  welcomeServer("welcome", corpus_rv = corpus_rv, parent = session)

  # 兼容接口: 下游模块继续按 reactive() 消费, 不感知 reactiveVal 差异
  corpus <- reactive(corpus_rv())

  # 未上传数据时全局显示中文占位提示（spec §5）
  # FINDING-009 修复: 用户已经在「数据导入」页时, 不再显示"请先在数据导入
  # 页上传文件"的全局提示, 避免"已经在你要去的地方却让你去那里"的认知失调.
  # 在 welcome 页同理 — 用户正在选择入门路径, 不需要被这条提示打扰.
  output$global_hint <- renderUI({
    cur <- input$menu
    on_landing <- is.null(cur) || identical(cur, "upload") || identical(cur, "welcome")
    if (is.null(corpus()) && !on_landing) empty_hint()
  })

  # session 级 PDF 输出目录, 跨会话隔离, session 结束自动清理
  session_pdf_dir <- file.path(tempdir(), "pdf_jobs", session$token)
  dir.create(session_pdf_dir, recursive = TRUE, showWarnings = FALSE)
  session$onSessionEnded(function() unlink(session_pdf_dir, recursive = TRUE))

  # 既有 8 个模块 (Phase 1)
  overviewServer("overview", corpus)
  sourcesServer("sources", corpus)
  authorsServer("authors", corpus)
  documentsServer("documents", corpus)
  conceptualServer("conceptual", corpus)
  intellectualServer("intellectual", corpus)
  socialServer("social", corpus)

  # v0.6: PRISMA 流程图 + 报告导出
  prismaServer("prisma", corpus, shared)
  reportServer("report", corpus, shared)

  # AI 助手 (Phase 2)
  aiScreenServer("ai_screen", corpus, shared)
  pdfFetchServer("pdf_fetch", corpus, shared, session_dir = session_pdf_dir)
  aiTranslateServer("ai_translate", corpus, shared)
  aiSummaryServer("ai_summary", corpus, shared)
  aiReviewServer("ai_review", corpus, shared)
  aiRewriteServer("ai_rewrite", shared)
  aiChatServer("ai_chat", corpus, shared)
  aiCiteServer("ai_cite", corpus, shared)
  settingsServer("settings", shared)
}

shinyApp(ui, server)
