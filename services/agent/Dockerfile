# services/agent — FastAPI 唯一后端
# 基础镜像 debian bookworm-slim 自带 bash (compose 启动串 alembic && uvicorn / demo 串需要);
# alembic 已在 requirements.txt, 随 pip 装入镜像。
FROM python:3.12-slim

# 可选 PyPI 镜像源 (默认官方 pypi.org, 行为不变)。
# 网络受限环境可传 --build-arg PIP_INDEX_URL=... --build-arg PIP_TRUSTED_HOST=... 加速/绕墙。
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

WORKDIR /app
COPY requirements.txt .
# 仅当 build-arg 给定时才注入 --index-url / --trusted-host, 否则走 pip 默认官方源。
RUN set -eux; \
    EXTRA=""; \
    if [ -n "$PIP_INDEX_URL" ]; then EXTRA="--index-url $PIP_INDEX_URL"; fi; \
    if [ -n "$PIP_TRUSTED_HOST" ]; then EXTRA="$EXTRA --trusted-host $PIP_TRUSTED_HOST"; fi; \
    pip install --no-cache-dir $EXTRA -r requirements.txt

COPY app/ app/
# 迁移 + CLI 脚本 (供 alembic upgrade / run_agent_e2e --offline-fixtures builtin /
# verify_runlog / eval_grounding 在容器内运行)
COPY alembic.ini .
COPY migrations/ migrations/
COPY scripts/ scripts/

# 非 root 运行 (Codex step5-P2)。
# 预建 /data/corpora 并 chown 给 appuser: 命名卷首次挂载到空卷时会继承镜像内该路径的属主,
# 使非 root 的 agent/demo 能写缓存 markdown / RunLog / 指标 (修 PermissionError)。
RUN useradd -m -u 10001 appuser \
    && mkdir -p /data/corpora \
    && chown -R appuser /app /data/corpora
USER appuser

ENV R_ANALYSIS_URL=http://r-analysis:8001
ENV PORT=8000
EXPOSE 8000

# healthcheck 读 PORT 环境变量, 不硬编码 (Codex step5-P2)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://localhost:{p}/healthz',timeout=4).status==200 else 1)"]

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
