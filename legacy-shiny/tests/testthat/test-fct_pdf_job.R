# R3-2: fct_pdf_job.R 单测 (DOI 白名单 + session 隔离)
source(file.path("..", "..", "R", "fct_env.R"))
source(file.path("..", "..", "R", "fct_pdf_job.R"))

test_that("pdf_job_new 拒绝非法 DOI", {
  td <- withr::local_tempdir()
  expect_error(pdf_job_new("nonsense", session_dir = td), "DOI")
  expect_error(pdf_job_new("10.1/a; rm -rf /", session_dir = td), "DOI")
  expect_error(pdf_job_new(c("10.1016/j.joi.2017.08.007", "$(injection)"),
                            session_dir = td), "DOI")
})

test_that("pdf_job_new 接受合法 DOI 并初始化目录文件", {
  td <- withr::local_tempdir()
  job <- pdf_job_new("10.1016/j.joi.2017.08.007", session_dir = td)
  expect_named(job, c("dois","session_dir","out_dir","worklist","dois_file","proc","status"))
  expect_true(dir.exists(job$out_dir))
  expect_true(file.exists(job$dois_file))
  expect_equal(readLines(job$dois_file), "10.1016/j.joi.2017.08.007")
  expect_equal(job$status, "pending")
})

test_that("pdf_job_new 拒绝共享目录 /srv/shared/pdfs", {
  expect_error(pdf_job_new("10.1/x.y.z", session_dir = "/srv/shared/pdfs"),
               "session.*隔离")
})

test_that("pdf_job_kill 在未启动时不崩", {
  td <- withr::local_tempdir()
  job <- pdf_job_new("10.1016/j.joi.2017.08.007", session_dir = td)
  killed <- pdf_job_kill(job)
  expect_equal(killed$status, "cancelled")
})

test_that("pdf_job_run 在 lit_pipeline.py 不存在时降级", {
  td <- withr::local_tempdir()
  job <- pdf_job_new("10.1016/j.joi.2017.08.007", session_dir = td)
  bogus_cfg <- list(lit_pipeline_path = "/no/such/lit_pipeline.py",
                    python_bin = "python3")
  expect_warning(out <- pdf_job_run(job, cfg = bogus_cfg), "降级")
  expect_equal(out$status, "skipped")
})
