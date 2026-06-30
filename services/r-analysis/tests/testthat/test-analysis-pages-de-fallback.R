test_that("documents_dto falls back to DE split for bibliocn records", {
  M <- data.frame(
    AU = c("A", "B"),
    TI = c("T1", "T2"),
    SO = c("S1", "S2"),
    PY = c(2024L, 2025L),
    DE = c("MEDICINE; HEALTH CARE", "MEDICINE; DIGITAL HEALTH"),
    TC = c(2L, 1L),
    stringsAsFactors = FALSE
  )
  class(M) <- c("bibliometrixDB", "data.frame")
  attr(M, "dbsource") <- "bibliocn"

  d <- documents_dto(M, top = 10L)

  expect_true(length(d$keywords) >= 3L)
  expect_equal(d$keywords[[1]]$term, "MEDICINE")
  expect_equal(d$keywords[[1]]$freq, 2L)
})
