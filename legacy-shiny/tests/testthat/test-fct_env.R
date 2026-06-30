# R1-1: fct_env.R 单测（.env 加载 / key 存在判定 / config 读取 / 不暴露 key）
source(file.path("..", "..", "R", "fct_env.R"))

test_that("load_env 读取 .env 并写入 Sys 环境", {
  td <- withr::local_tempdir()
  envfile <- file.path(td, ".env")
  writeLines(c("FOO=bar", "BAZ=qux", "# comment line", "", "QUOTED=\"hello world\""),
             envfile)
  withr::with_envvar(c(FOO = NA, BAZ = NA, QUOTED = NA), {
    load_env(envfile)
    expect_equal(Sys.getenv("FOO"), "bar")
    expect_equal(Sys.getenv("BAZ"), "qux")
    expect_equal(Sys.getenv("QUOTED"), "hello world")  # 引号被去掉
  })
})

test_that("load_env 文件不存在时静默返回", {
  expect_silent(load_env("/no/such/.env"))
})

test_that("has_env 仅返回是否存在，不暴露值", {
  withr::with_envvar(c(MYKEY = "secret-value"), {
    expect_true(has_env("MYKEY"))
    expect_false(has_env("NONEXISTENT_KEY"))
  })
})

test_that("get_env_value 拿值；未设置时 stop", {
  withr::with_envvar(c(TMPKEY = "val1"), {
    expect_equal(get_env_value("TMPKEY"), "val1")
  })
  expect_error(get_env_value("UNSET_KEY_XYZ"), "未配置")
})

test_that("get_env_value 不缓存：环境变化即生效", {
  withr::with_envvar(c(DYN = "v1"), {
    expect_equal(get_env_value("DYN"), "v1")
  })
  withr::with_envvar(c(DYN = "v2"), {
    expect_equal(get_env_value("DYN"), "v2")
  })
})

test_that("get_llm_config 从 config.yml 取默认 provider 与 model", {
  cfg <- get_llm_config(path = file.path("..", "..", "config.yml"))
  expect_type(cfg, "list")
  expect_equal(cfg$default_provider, "deepseek")
  expect_true("deepseek-v4-flash" %in% cfg$providers$deepseek$models)
  expect_equal(cfg$providers$deepseek$default_model, "deepseek-v4-flash")
  # 三档价目齐全（codex 修订）
  pr <- cfg$providers$deepseek$pricing$`deepseek-v4-flash`
  expect_true(all(c("input_cache_hit","input_cache_miss","output","currency") %in% names(pr)))
})
