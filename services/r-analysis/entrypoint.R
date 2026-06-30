# services/r-analysis/entrypoint.R — 启动 plumber 服务
# 运行: Rscript entrypoint.R  (工作目录须为 services/r-analysis, 见 Dockerfile WORKDIR)
library(plumber)
port <- as.integer(Sys.getenv("PORT", unset = "8001"))
message(sprintf("[r-analysis] 启动于 0.0.0.0:%d", port))
plumber::plumb("plumber.R")$run(host = "0.0.0.0", port = port)
