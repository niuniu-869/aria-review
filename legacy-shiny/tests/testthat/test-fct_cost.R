# R1-2: fct_cost.R 单测（三档计费 + reactive 累加 + csv 导出）
source(file.path("..", "..", "R", "fct_env.R"))
source(file.path("..", "..", "R", "fct_cost.R"))

cfg_path <- file.path("..", "..", "config.yml")

test_that("cost_log_empty 返回正确 schema", {
  df <- cost_log_empty()
  expect_s3_class(df, "data.frame")
  expect_equal(nrow(df), 0L)
  expect_setequal(names(df),
    c("ts","provider","model",
      "prompt_tokens_hit","prompt_tokens_miss","completion_tokens","cost_cny"))
})

test_that("cost_estimate 按 deepseek-v4-flash 三档计算", {
  cfg <- get_llm_config(cfg_path)
  # 1M cache_hit only -> 0.02 CNY
  expect_equal(
    cost_estimate("deepseek", "deepseek-v4-flash",
                  prompt_tokens_hit = 1e6, prompt_tokens_miss = 0,
                  completion_tokens = 0, cfg = cfg),
    0.02, tolerance = 1e-9)
  # 1M cache_miss + 1M output -> 1.00 + 2.00 = 3.00
  expect_equal(
    cost_estimate("deepseek", "deepseek-v4-flash",
                  prompt_tokens_hit = 0, prompt_tokens_miss = 1e6,
                  completion_tokens = 1e6, cfg = cfg),
    3.00, tolerance = 1e-9)
})

test_that("cost_estimate v4-pro 在优惠期内用折扣价", {
  cfg <- get_llm_config(cfg_path)
  # 优惠期内 (本测试基准时间 2026-05-20 < 2026-05-31)
  est <- cost_estimate("deepseek", "deepseek-v4-pro",
                       prompt_tokens_hit = 0, prompt_tokens_miss = 1e6,
                       completion_tokens = 0, cfg = cfg)
  expect_equal(est, 3.00, tolerance = 1e-9)  # 折扣价 cache_miss=3.00
})

test_that("cost_estimate 未知 provider/model 返回 NA", {
  cfg <- get_llm_config(cfg_path)
  expect_true(is.na(cost_estimate("unknown", "x", 0, 0, 0, cfg = cfg)))
  expect_true(is.na(cost_estimate("deepseek", "bogus-model", 0, 0, 0, cfg = cfg)))
})

test_that("cost_add 通过 shared$cost_log 追加一行 + 算出 cost_cny", {
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  shiny::isolate({
    cost_add(shared, "deepseek", "deepseek-v4-flash",
             usage = list(prompt_tokens_hit = 100,
                          prompt_tokens_miss = 200,
                          completion_tokens = 50),
             cfg = get_llm_config(cfg_path))
    expect_equal(nrow(shared$cost_log), 1L)
    expect_equal(shared$cost_log$completion_tokens, 50L)
    expect_gt(shared$cost_log$cost_cny, 0)
    # 累加多次
    cost_add(shared, "deepseek", "deepseek-v4-flash",
             usage = list(prompt_tokens_hit = 0,
                          prompt_tokens_miss = 0,
                          completion_tokens = 100),
             cfg = get_llm_config(cfg_path))
    expect_equal(nrow(shared$cost_log), 2L)
  })
})

test_that("cost_export_csv 写出可读 CSV", {
  shared <- shiny::reactiveValues(cost_log = cost_log_empty())
  shiny::isolate({
    cost_add(shared, "deepseek", "deepseek-v4-flash",
             usage = list(prompt_tokens_hit = 0,
                          prompt_tokens_miss = 100,
                          completion_tokens = 50),
             cfg = get_llm_config(cfg_path))
    tf <- tempfile(fileext = ".csv")
    cost_export_csv(shared$cost_log, tf)
    rd <- utils::read.csv(tf)
    expect_equal(nrow(rd), 1L)
    expect_equal(rd$model, "deepseek-v4-flash")
  })
})
