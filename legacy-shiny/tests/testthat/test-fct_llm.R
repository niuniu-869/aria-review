# R1-4: fct_llm.R 单测（provider 分发 + 脱敏）
source(file.path("..", "..", "R", "fct_env.R"))
source(file.path("..", "..", "R", "fct_llm_deepseek.R"))
source(file.path("..", "..", "R", "fct_llm.R"))

test_that("llm_call 分发到 deepseek_chat", {
  mockery::stub(llm_call, "deepseek_chat",
                function(messages, ...) {
                  list(text = "ok", model = "m", finish_reason = "stop",
                       usage = list(prompt_tokens_hit = 0L,
                                    prompt_tokens_miss = 1L,
                                    completion_tokens = 1L))
                })
  out <- llm_call("deepseek", messages = list(list(role = "user", content = "hi")))
  expect_equal(out$text, "ok")
})

test_that("llm_call 未知 provider 立即 stop", {
  expect_error(llm_call("unknown", messages = list()), "未知 provider")
})

test_that("safe_log_error 脱敏 API key / Bearer / Authorization", {
  leaked_key <- paste0("s", "k-", "leak-key-here")
  err <- structure(list(message = paste("Bearer", leaked_key, "Authorization: Bearer x")),
                   class = c("simpleError", "error", "condition"))
  msg <- safe_log_error(err)
  expect_false(grepl(leaked_key, msg, fixed = TRUE))
  expect_false(grepl("leak-key", msg, fixed = TRUE))
  expect_true(grepl("REDACTED", msg))
})

test_that("safe_log_error 接受字符串与 condition 两种入参", {
  first_key <- paste0("s", "k-", "abc123")
  second_key <- paste0("s", "k-", "deadbeef")
  expect_true(grepl("REDACTED", safe_log_error(paste(first_key, "in log"))))
  expect_true(grepl("REDACTED",
                    safe_log_error(simpleError(second_key))))
})
