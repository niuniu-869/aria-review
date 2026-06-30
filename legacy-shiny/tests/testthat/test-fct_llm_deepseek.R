# R1-3: fct_llm_deepseek.R 单测
source(file.path("..", "..", "R", "fct_env.R"))
source(file.path("..", "..", "R", "fct_llm_deepseek.R"))

# 构造一个伪 httr2_response (含 body 与 status)
.fake_resp <- function(body_str, status = 200L) {
  list(
    method = "POST",
    url = "https://api.deepseek.com/v1/chat/completions",
    status_code = status,
    headers = list(`content-type` = "application/json"),
    body = charToRaw(body_str),
    request = NULL,
    cache = new.env()
  ) |>
    structure(class = c("httr2_response", "S7_object"))
}

test_that("deepseek_chat 解析响应字段 (text / usage / model)", {
  body <- '{
    "id":"x","object":"chat.completion","model":"deepseek-v4-flash",
    "choices":[{"index":0,"message":{"role":"assistant","content":"hi"},
                "finish_reason":"stop"}],
    "usage":{"prompt_tokens":12,"completion_tokens":1,
             "prompt_tokens_details":{"cached_tokens":4}}
  }'
  withr::with_envvar(c(DEEPSEEK_API_KEY = "test-api-key",
                       DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"), {
    mockery::stub(deepseek_chat, "httr2::req_perform",
                  function(req) .fake_resp(body))
    out <- deepseek_chat(messages = list(list(role = "user", content = "ping")),
                        model = "deepseek-v4-flash")
    expect_equal(out$text, "hi")
    expect_equal(out$usage$prompt_tokens_hit, 4L)
    expect_equal(out$usage$prompt_tokens_miss, 8L)  # 12 total - 4 cached
    expect_equal(out$usage$completion_tokens, 1L)
    expect_equal(out$model, "deepseek-v4-flash")
    expect_equal(out$finish_reason, "stop")
  })
})

test_that("deepseek_chat 在缺 key 时立即 stop", {
  withr::with_envvar(c(DEEPSEEK_API_KEY = ""), {
    expect_error(
      deepseek_chat(messages = list(list(role = "user", content = "x"))),
      "DEEPSEEK_API_KEY"
    )
  })
})

test_that("deepseek_chat 强制 max_tokens 与 json_mode 进入请求体", {
  captured <- NULL
  body <- '{"choices":[{"message":{"content":"{\\"ok\\":true}"}}],
            "usage":{"prompt_tokens":1,"completion_tokens":1}}'
  withr::with_envvar(c(DEEPSEEK_API_KEY = "test-api-key",
                       DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"), {
    mockery::stub(deepseek_chat, "httr2::req_perform",
                  function(req) { captured <<- req; .fake_resp(body) })
    deepseek_chat(messages = list(list(role = "user", content = "x")),
                  max_tokens = 256, json_mode = TRUE)
  })
  expect_true(!is.null(captured))
  # httr2 把请求 body 保留为 R list, 直到 perform 时才序列化
  sent_body <- captured$body$data
  expect_equal(as.integer(sent_body$max_tokens), 256L)
  expect_equal(sent_body$response_format$type, "json_object")
  expect_false(isTRUE(sent_body$stream))
})
