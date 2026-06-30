# BiblioCN — 中文文献计量分析平台

## 项目简介

BiblioCN 是一个基于 R Shiny 构建的中文文献计量分析平台，底层调用 `bibliometrix` 包提供的分析引擎。平台支持从 Web of Science（WoS）、Scopus 等数据库导出的文献数据，提供从数据导入到多维度可视化分析的完整工作流，帮助研究人员快速掌握某一领域的知识结构与发展脉络。

---

## 功能列表

平台包含以下 8 个分析页：

1. **数据导入**：支持上传 WoS 纯文本（.txt）和 Scopus CSV（.csv）格式文件，自动解析并转换为分析用数据框。
2. **概览（Overview）**：展示文献基本统计信息，包括文献数量、作者数、关键词数、平均被引次数、年度产出趋势等。
3. **来源分析（Sources Analysis）**：分析期刊/来源分布，展示 Bradford 定律、期刊影响力排名及核心期刊列表。
4. **作者分析（Authors Analysis）**：统计作者生产力，包括高产作者排名、洛特卡定律拟合、作者 h 指数分布。
5. **文档与关键词（Documents & Keywords）**：提供文献关键词词云、词频统计、关键词演变趋势及关键词共现分析。
6. **概念结构（Conceptual Structure）**：通过共词分析、对应分析或主题聚类，绘制研究领域的主题图（Thematic Map）和战略图。
7. **知识结构（Intellectual Structure）**：基于共被引分析和文献耦合，揭示领域知识基础与研究前沿，生成知识图谱。
8. **社会结构（Social Structure）**：构建作者合作网络、机构合作网络及国家合作网络，可视化学术合作关系。

---

## 安装与运行

### 前置要求

- R >= 4.3.0
- 推荐使用 [renv](https://rstudio.github.io/renv/) 管理依赖

### 安装步骤

```r
# 1. 克隆本仓库
# git clone <repo-url>
# cd biblio_cn

# 2. 恢复 renv 依赖环境（首次运行）
renv::restore()

# 3. 启动应用
shiny::runApp()
```

应用默认在本地 `http://127.0.0.1:xxxx` 启动，浏览器会自动打开。

---

## 部署说明

### Shiny Server（自托管）

1. 安装 [Shiny Server](https://www.rstudio.com/products/shiny/shiny-server/)。
2. 将项目目录复制到 `/srv/shiny-server/biblio_cn/`。
3. 确保服务器 R 环境已安装所有依赖（或使用 `renv::restore()` 恢复）。
4. 重启 Shiny Server：`sudo systemctl restart shiny-server`。

### shinyapps.io（云端托管）

```r
# 安装部署工具
install.packages("rsconnect")

# 配置账户（在 shinyapps.io 获取 Token）
rsconnect::setAccountInfo(name = "<账户名>", token = "<Token>", secret = "<Secret>")

# 部署
rsconnect::deployApp(appDir = ".", appName = "biblio_cn")
```

---

## 致谢与引用

本平台的文献计量分析功能由 [bibliometrix](https://www.bibliometrix.org/) 包提供支持。如果您在学术研究中使用本平台，请同时引用 bibliometrix：

> Aria, M. & Cuccurullo, C. (2017). **bibliometrix: An R-tool for comprehensive science mapping analysis**. *Journal of Informetrics*, 11(4), 959–975. https://doi.org/10.1016/j.joi.2017.08.007

---

## 许可证说明

- **本项目**（BiblioCN 应用代码）采用 [MIT 许可证](LICENSE) 发布，允许自由使用、修改和分发。
- **运行时依赖**：本平台在运行时调用 `bibliometrix` 包，该包采用 GPL-3 许可证。BiblioCN 与 bibliometrix 为**依赖关系而非衍生作品**，BiblioCN 自身代码不受 GPL-3 约束。部署时请遵守各依赖包的各自许可证条款。

---

## 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库并创建功能分支：`git checkout -b feat/your-feature`。
2. 提交更改前请确保测试通过：`testthat::test_dir("tests/testthat/")`。
3. 遵循现有代码风格（函数命名：`fct_*`，模块命名：`mod_*`）。
4. 提交 PR 时请填写清晰的变更说明。

如有问题，欢迎通过 Issue 讨论。
