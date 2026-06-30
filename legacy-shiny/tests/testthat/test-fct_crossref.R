# R2-4: fct_crossref.R 单测 (L1 mock + L3 真 DOI)
source(file.path("..", "..", "R", "fct_crossref.R"))

.fake_resp <- function(body_str, status = 200L) {
  list(status_code = status,
       headers = list(`content-type` = "application/json"),
       body = charToRaw(body_str)) |>
    structure(class = c("httr2_response", "S7_object"))
}

test_that("crossref_lookup 解析标准字段 (mock)", {
  body <- '{
    "message":{
      "DOI":"10.1016/j.joi.2017.08.007",
      "title":["bibliometrix: An R-tool"],
      "author":[{"given":"Massimo","family":"Aria"},
                {"given":"Corrado","family":"Cuccurullo"}],
      "published-print":{"date-parts":[[2017,11]]},
      "container-title":["Journal of Informetrics"]
    }
  }'
  mockery::stub(crossref_lookup, "httr2::req_perform",
                function(req) .fake_resp(body))
  m <- crossref_lookup("10.1016/j.joi.2017.08.007")
  expect_equal(m$doi, "10.1016/j.joi.2017.08.007")
  expect_equal(m$year, 2017L)
  expect_equal(length(m$authors), 2L)
  expect_true(grepl("Aria", m$authors[1]))
  expect_equal(m$title, "bibliometrix: An R-tool")
  expect_equal(m$journal, "Journal of Informetrics")
})

test_that("verify_citations 批量返回 valid 列", {
  mockery::stub(verify_citations, "crossref_lookup",
                function(doi, ...) {
                  if (doi == "ok") list(doi = "ok")
                  else stop("simulated 404")
                })
  out <- verify_citations(c("ok", "bad"))
  expect_s3_class(out, "data.frame")
  expect_equal(out$doi, c("ok", "bad"))
  expect_equal(out$valid, c(TRUE, FALSE))
})

test_that("L3 真 DOI 校验 (RUN_LIVE_LLM=true 才跑)", {
  testthat::skip_if_not(Sys.getenv("RUN_LIVE_LLM") == "true",
                        "需 RUN_LIVE_LLM=true")
  m <- crossref_lookup("10.1016/j.joi.2017.08.007")
  expect_equal(m$doi, "10.1016/j.joi.2017.08.007")
  expect_equal(m$year, 2017L)
  expect_true(length(m$authors) >= 1L)
})
