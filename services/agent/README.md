# services/agent

FastAPI 唯一后端：前端的唯一入口，负责 agent 编排、LLM、R 分析服务代理、Postgres 持久化和 RunLog 校验。

## 端点 (公共契约 packages/contracts/openapi.yaml)
| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | /healthz | 自身 + 下游 R 可达性 (rService up/down) |
| POST | /projects/{pid}/corpus | 上传→转发 R /parse, 返回 CorpusRef |
| GET  | /projects/{pid}/corpus/{cid} | 语料状态 |
| GET  | /projects/{pid}/corpus/{cid}/overview | 概览 (注入 projectId) |

## 运行依赖

- Postgres：项目、论文、附件、RunLog 和 Agent 状态都落库。
- R 分析服务：`/healthz` 会检查 `R_ANALYSIS_URL` 指向的服务是否可达。
- LLM/Sciverse/Image key 都是可选项；不配置时走 FakeLLM 或功能降级。

Docker Compose 默认把 Postgres 暴露到宿主 `127.0.0.1:55432`，并创建
`bibliocn` 与 `bibliocn_test` 两个库。需要改端口时在仓库根 `.env` 写入
`POSTGRES_PORT=55433`。

## 本地运行
```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn
export R_ANALYSIS_URL=http://localhost:8001
python scripts/wait_for_db.py --timeout 60
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

服务内 `.env` 会被自动加载。排查干净复现时先确认 `services/agent/.env` 是否存在，
避免本机隐藏 key 或数据库地址影响结果。

## 测试

pytest 会 mock R 服务，但需要 Postgres 测试库：

```bash
docker compose up -d postgres
cd services/agent
DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn \
TEST_DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn_test \
PYTHONPATH=. python3 -m pytest -q
```

如果复用旧 `pgdata` 卷时缺少 `bibliocn_test`，执行
`docker compose exec postgres createdb -U bibliocn bibliocn_test` 补建即可。
