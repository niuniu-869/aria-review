# Contributing

Thanks for improving Aria Review (工程代号 BiblioCN).

## Setup

```bash
docker compose config -q
docker compose run --rm --build demo
docker compose up -d --build
curl http://localhost:8000/healthz
```

Compose Postgres 默认使用宿主 `127.0.0.1:55432`，避免占用本机常见的
`5432`。需要改端口时在根目录 `.env` 写入 `POSTGRES_PORT=55433`。

For frontend-only work:

```bash
pnpm -C apps/web install
pnpm -C apps/web dev
```

For backend work:

```bash
docker compose up -d postgres
cd services/agent
python3 -m pip install -r requirements.txt
DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn \
TEST_DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn_test \
python3 -m pytest -q
```

## Quality Gates

Run the smallest relevant checks for your change. Before a release branch, run:

```bash
docker compose config -q
pnpm -C apps/web test
pnpm -C apps/web build
cd services/agent && \
  DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn \
  TEST_DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn_test \
  python3 -m pytest -q
```

R tests require local R dependencies:

```bash
Rscript -e 'testthat::test_dir("services/r-analysis/tests/testthat")'
```

## Engineering Rules

- Keep changes scoped. Avoid unrelated refactors.
- Prefer existing patterns over new abstractions.
- Do not commit generated caches, local screenshots, `.env` files, or API keys.
- Update `packages/contracts/openapi.yaml` and regenerated frontend types together when API shape changes.
- Keep docs aligned with user-visible workflows.

## Commit Hygiene

Do not use `git add -A` blindly. Stage intentional files only.

Suggested commit style:

```text
feat(agent): add grounded review runlog export
fix(web): keep provenance anchor click inside source viewer
docs: refresh open-source README
```
