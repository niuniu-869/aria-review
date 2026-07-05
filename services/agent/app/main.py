"""BiblioCN agent — 唯一后端 (D5)。

Phase 0 范围: healthz + 上传→解析→概览 的代理 (切片1)。
- corpus 真值在 r-analysis 服务 (RDS), agent 暂不接 Postgres (无用户/项目/对话前 YAGNI;
  Postgres 见路线图 T11)。projectId 当前仅透传锚点。
- 真异步 (202 parsing + 后台) 留给 T10; Phase 0 同步代理, 202 直接带 ready/failed。
- 共享 httpx 客户端走连接池 (Codex step3-P2); R 错误码统一映射 (Codex step3-P1)。
"""
from __future__ import annotations
import base64
import asyncio
import json
import os
import re
import tempfile
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from .config import settings
from .errors import ApiError
from .cite_check import check_citations
from .llm import LLMError, _normalize_base_url, get_llm_client
from .net_safety import normalize_external_url
from .logging_setup import configure_logging, get_logger
from .prisma import build_prisma
from .refs import extract_papers
from .db import SessionLocal, engine, get_session
from .authz import global_guard
from .auth import get_current_user
from .agent.context import AgentContext
from .agent.prompts import AGENT_SYSTEM, WRAP_UP
from .agent.registry_factory import build_registry
from .agent.run_controller import RunController
from .agent.runlog import build_runlog
from .agent.metrics import grounding_metrics
from .review.load import project_corpus_content_hashes
from .harness.config import get_config
from .harness.events import SubscribableEventPublisher
from .harness.llm import LLMRouter
from .llm import override_from_key
from .repositories import agent_run as agent_run_repo
from .repositories import ai_job as ai_job_repo
from .repositories import library as lib_repo
from .repositories import project as project_repo
from .review.load import load_project_corpus
from .review.orchestrate import run_review
from .review.templates import get_template
from .run_status import normalize_run_status
from .prompts import (
    REVIEW_TYPES,
    REWRITE_ACTIONS,
    TRANSLATE_DIRECTIONS,
    build_review_context,
    prompt_chat,
    prompt_extract_structured,
    prompt_review,
    prompt_rewrite,
    prompt_screen,
    prompt_summary,
    prompt_translate,
    review_template,
)
from .report import (
    PandocFailed,
    PandocTimeout,
    PandocUnavailable,
    build_report,
    probe_pandoc,
)
from .r_client import RClient
from .schemas import (
    AgentRunRef,
    AgentRunRequest,
    ArtifactCreateRequest,
    ArtifactItem,
    ArtifactPatchRequest,
    AiJobCreateRequest,
    AiJobItem,
    AuthorProductionEnvelope,
    AuthorsResult,
    ChatRequest,
    CitedRefsEnvelope,
    CiteResult,
    ConfirmRequest,
    CorpusMaterializeResponse,
    CorpusRef,
    DocumentsResult,
    EvolutionEnvelope,
    Health,
    HistciteEnvelope,
    ImportFailedItem,
    InclusionBreakdown,
    InclusionPatchRequest,
    ImagePingResult,
    ImageSettingsPayload,
    KeywordTrendEnvelope,
    LibraryStats,
    NetworkResult,
    OcrBreakdown,
    OverviewResult,
    PaperDetail,
    PapersImportResponse,
    PublicStats,
    PrismaRequest,
    PrismaResult,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectLibraryStats,
    ProjectPaperItem,
    ProjectRef,
    RefsRequest,
    ReportOptions,
    ReviewRequest,
    RewriteRequest,
    RunControlResponse,
    RunDetail,
    ScreenRequest,
    ScreenResult,
    SocialResult,
    SourcesResult,
    StructureResponse,
    SummaryRequest,
    TextResult,
    ThematicEnvelope,
    ThreeFieldEnvelope,
    TopicRequest,
    TranslateRequest,
    FromSearchCandidate,
    FromSearchFailedItem,
    FromSearchRequest,
    FromSearchResult,
    SciverseAgenticSearchRequest,
    SciverseAgenticSearchResult,
    SciverseBackfillFulltextRequest,
    SciverseBackfillFulltextResult,
    SciverseBackfillFailedItem,
    SciverseFetchContentRequest,
    SciverseFetchContentResult,
    SciverseMetaSearchRequest,
    SciverseMetaSearchResult,
    SciversePingResult,
    SciverseSettingsPayload,
    BackfillMetadataRequest,
    BackfillMetadataResult,
    ExtractStructuredRequest,
    ExtractStructuredResult,
)
from .ingest.fulltext import ingest_pdfs
from .ingest.sciverse_fulltext import (
    fetch_and_store_sciverse_content,
    fetch_sciverse_markdown,
    select_sciverse_backfill_candidates,
    store_sciverse_markdown,
)
from .ingest.search_metadata import parse_cited_by_count
from .repositories import corpus as corpus_repo
from .services import project_svc
from .services.metadata_backfill import backfill_paper_metadata
from .services.extraction import (
    count_no_fulltext_candidates,
    extract_paper_structured,
    fulltext_paper_ids_subquery,
    is_no_fulltext_skip_reason,
)
from .sciverse import (
    SciverseClient,
    normalize_agentic_hit,
    normalize_meta_result,
    sciverse_config,
)

configure_logging()
log = get_logger("agent")

_RID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # since 日期白名单 (防注入 OpenAlex filter)

# R 状态码 → (契约 code, 公共 HTTP 状态码) 统一映射 (Codex step3-P1)
_ERR_MAP = {
    400: ("VALIDATION_ERROR", 400),
    404: ("CORPUS_NOT_FOUND", 404),
    409: ("CORPUS_NOT_READY", 409),
    413: ("PAYLOAD_TOO_LARGE", 413),
    415: ("UNSUPPORTED_FILE", 415),
    422: ("PARSE_FAILED", 422),
    502: ("ANALYSIS_FAILED", 502),
    503: ("R_SERVICE_UNAVAILABLE", 503),
}


def _short_error_message(value: object, max_length: int = 300) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        text = type(value).__name__ if value is not None else "未知错误"
    return text if len(text) <= max_length else text[:max_length - 1] + "…"


def _raise_from_r(status: int, body: dict | None):
    code = (body or {}).get("code")
    msg = (body or {}).get("message", "上游错误")
    if status in _ERR_MAP:
        default_code, sc = _ERR_MAP[status]
        raise ApiError(sc, code or default_code, msg)
    raise ApiError(502, "ANALYSIS_FAILED", msg)


async def _proxy_get(r_method, corpus_id: str, project_id: str) -> dict:
    """分析端点共用代理: 调 R, 映射错误, 注入 projectId (DRY)。"""
    status, body = await r_method(corpus_id)
    if status != 200:
        _raise_from_r(status, body)
    body = dict(body or {})
    body["projectId"] = project_id
    return body


async def _proxy_envelope(r_method, corpus_id: str, project_id: str) -> dict:
    """A4 高级图端点共用代理: 透传 R 的可用性信封。

    与 _proxy_get 的区别: R 端返回 200 + 信封时, available:false 也是 200 (正常降级),
    **不** 当错误处理。仅在语料级前置错误 (404/409/422 等非 200) 时映射错误码。
    """
    status, body = await r_method(corpus_id)
    if status != 200:
        _raise_from_r(status, body)
    body = dict(body or {})
    body["projectId"] = project_id
    return body


@asynccontextmanager
async def lifespan(app: FastAPI):
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    async with httpx.AsyncClient(base_url=settings.r_analysis_url,
                                 timeout=timeout, limits=limits) as client:
        app.state.r_client = RClient(client)

        # A7: 启动时探测 pandoc 是否可用并缓存 (DOCX 导出依赖)。
        # 缺失时 format=docx 端点返回 503, 前端据此降级仅显示 md/html。
        app.state.pandoc_ok = probe_pandoc()
        if not app.state.pandoc_ok:
            log.warning("pandoc 不可用, DOCX 报告导出将降级 (返回 503)")

        # P1-6: 装配 agent run 状态
        app.state.publisher = SubscribableEventPublisher()
        _llm_router = LLMRouter.from_config()
        _engine_config = get_config()
        _default_model = (
            getattr(settings, "deepseek_model", None)
            or "deepseek-chat"
        )

        async def _build_ctx(project_id: int) -> AgentContext:
            registry = build_registry(SessionLocal, app.state.r_client)
            return AgentContext(
                registry=registry,
                llm_router=_llm_router,
                model_names=[_default_model],
                system_prompt=AGENT_SYSTEM,
                tool_ids=None,
                max_rounds=6,
                wrap_up_prompt=WRAP_UP,
            )

        app.state.run_controller = RunController(
            SessionLocal, app.state.publisher, _build_ctx
        )

        # P3-1: 启动时回收孤儿 run（上次进程崩溃/重启残留的 status=running，已无驱动 task）
        # → 统一标 failed，避免前端永久 spinner / SSE 空等。
        async with SessionLocal() as s:
            await app.state.run_controller.recover_orphans(s)

        try:
            yield
        finally:
            # P3-1: 停机时取消所有活跃驱动 task 并等其收束（CancelledError 由 shutdown 内处理）。
            await app.state.run_controller.shutdown()
            # P3-1: 释放全局 engine 连接池（recover_orphans 在 startup 借过连接 → 归还到池）。
            # 在 lifespan 同一事件循环内 dispose，避免连接被 asyncpg GC 在已关闭的 loop 上
            # 清理而抛 "Event loop is closed"（短生命周期 loop 下的 TestClient 场景尤甚）。
            await engine.dispose()


app = FastAPI(
    title="BiblioCN agent", version="0.4.1", lifespan=lifespan,
    dependencies=[Depends(global_guard)],  # 全局守卫：认证 + project 归属（Round 5）
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,  # cookie 会话跨域必需（allow_origins 因此不能为 "*"）
    allow_methods=["*"],
    allow_headers=["*"],
)

# A5 研究副驾路由（GAP 发现 / 价值二次验证 / HITL，契约 §2.1）。routes_research 顶层不导入
# main（_llm 等在 handler 内 lazy import），故此处 include 无循环依赖。
from .routes_research import router as research_router  # noqa: E402
app.include_router(research_router)
from .routes_auth import router as auth_router  # noqa: E402 — 认证/账户/计费 (Phase B)
app.include_router(auth_router)


def get_r_client(request: Request) -> RClient:
    return request.app.state.r_client


@app.middleware("http")
async def request_id_mw(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    rid = incoming if _RID_RE.match(incoming) else str(uuid.uuid4())  # 消毒, 防头注入
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=exc.body())


# R 健康 grace 缓存：R 是单线程 plumber，一次检索会阻塞它数秒（健康探针被饿死、5s 超时）。
# 若仅凭单次探测失败就报 rService=down，前端会在每次正常检索时闪现"后端部分不可用"。
# 故：R 在 grace 秒内成功健康过 → 视为 up（容忍检索期短暂阻塞）；持续失败超过 grace 才报 down。
_r_health_last_ok = {"ts": 0.0}
_R_HEALTH_GRACE = float(os.environ.get("R_HEALTH_GRACE", "30"))


@app.get("/healthz", response_model=Health)
async def healthz(r: RClient = Depends(get_r_client)) -> Health:
    up = await r.health()
    now = time.monotonic()
    if up:
        _r_health_last_ok["ts"] = now
    elif now - _r_health_last_ok["ts"] < _R_HEALTH_GRACE:
        # 近期健康过 → 当前多半只是被检索阻塞，不翻 down（避免误报"部分不可用"）
        up = True
    return Health(status="ok", service="agent", rService="up" if up else "down")


@app.get("/public/stats", response_model=PublicStats)
async def public_stats(s=Depends(get_session)) -> PublicStats:
    """公开着陆页统计（authz 豁免，免认证）：真实入库规模，用于 welcome 页展示。"""
    from sqlalchemy import distinct, func, select
    from .models import DocumentStructure, Paper
    papers = await s.scalar(select(func.count()).select_from(Paper)) or 0
    dois = await s.scalar(
        select(func.count(distinct(Paper.doi))).where(
            Paper.doi.isnot(None), Paper.doi != "")
    ) or 0
    blocks = await s.scalar(
        select(func.coalesce(
            func.sum(func.json_array_length(DocumentStructure.content_list)), 0)
        ).where(DocumentStructure.content_list.isnot(None))
    ) or 0
    return PublicStats(papers=int(papers), blockAnchors=int(blocks), dois=int(dois))


@app.post("/projects/{project_id}/corpus")
async def create_corpus(
    project_id: str,
    request: Request,
    file: UploadFile,
    dbsource: str = Form(...),
    r: RClient = Depends(get_r_client),
):
    if dbsource not in ("wos", "scopus"):
        raise ApiError(400, "VALIDATION_ERROR", "dbsource 仅支持 wos/scopus")
    # 先看 Content-Length, 超限直接拒 (避免先吃满内存, Codex step3-P1)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > settings.max_upload_bytes:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "文件超过 50MB 上限")
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "文件超过 50MB 上限")
    if not content:
        raise ApiError(400, "VALIDATION_ERROR", "空文件")

    status, meta = await r.parse(content, file.filename or "upload.txt", dbsource)
    if status in (400, 413, 415):           # 客户端错误 → 透传
        _raise_from_r(status, meta)
    # 200 ready / 422 failed → 202 + CorpusRef (async: 资源已创建, status 反映结果)
    meta = dict(meta or {})
    meta["projectId"] = project_id
    ref = CorpusRef(**meta)                  # 校验对齐契约 (Codex step3-P1)
    return JSONResponse(status_code=202, content=ref.model_dump(exclude_none=True))


@app.post("/projects/{project_id}/corpus/from-topic")
async def corpus_from_topic(
    project_id: str, body: TopicRequest, r: RClient = Depends(get_r_client)
):
    """路径 A: 主题词 → OpenAlex 检索建库 (代理 r-analysis, 同步)。"""
    since = body.since if _DATE_RE.match(body.since) else "2016-01-01"
    status, meta = await r.from_topic(body.query, body.n, since, body.withRefs)
    if status != 200:
        _raise_from_r(status, meta)
    meta = dict(meta or {})
    meta["projectId"] = project_id
    ref = CorpusRef(**meta)  # 校验对齐契约 (额外键忽略)
    return JSONResponse(status_code=202, content=ref.model_dump(exclude_none=True))


@app.post("/projects/{project_id}/corpus/from-refs")
async def corpus_from_refs(
    project_id: str, body: RefsRequest, request: Request,
    r: RClient = Depends(get_r_client),
):
    """路径 B: 粘贴文本 → agent LLM 抽题录 → r-analysis OpenAlex 反查建库。"""
    papers = await extract_papers(_llm(request), body.text)
    if not papers:
        raise ApiError(422, "NO_PAPERS",
                       "未能从文本中识别出论文条目, 请补充标题/DOI (或确认已配置 AI key)")
    status, meta = await r.from_refs(papers, body.withRefs)
    if status != 200:
        _raise_from_r(status, meta)
    meta = dict(meta or {})
    meta["projectId"] = project_id
    ref = CorpusRef(**meta)
    payload = ref.model_dump(exclude_none=True)
    if meta.get("matched") is not None:  # 透传匹配统计供前端展示
        payload["matched"] = meta["matched"]
        payload["unmatched"] = meta.get("unmatched", 0)
    payload["extracted"] = len(papers)
    return JSONResponse(status_code=202, content=payload)


@app.get("/projects/{project_id}/corpus/{corpus_id}", response_model=CorpusRef)
async def get_corpus(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    status, body = await r.get_corpus(corpus_id)
    if status != 200:
        _raise_from_r(status, body)
    body = dict(body or {})
    body["projectId"] = project_id
    return body


@app.get("/projects/{project_id}/corpus/{corpus_id}/overview",
         response_model=OverviewResult)
async def get_overview(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    return await _proxy_get(r.get_overview, corpus_id, project_id)


@app.get("/projects/{project_id}/corpus/{corpus_id}/sources", response_model=SourcesResult)
async def get_sources(project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)):
    return await _proxy_get(r.get_sources, corpus_id, project_id)


@app.get("/projects/{project_id}/corpus/{corpus_id}/authors", response_model=AuthorsResult)
async def get_authors(project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)):
    return await _proxy_get(r.get_authors, corpus_id, project_id)


@app.get("/projects/{project_id}/corpus/{corpus_id}/documents", response_model=DocumentsResult)
async def get_documents(project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)):
    return await _proxy_get(r.get_documents, corpus_id, project_id)


# --- A4 高级图端点 (返回可用性信封; available:false 也是 HTTP 200) ---

@app.get(
    "/projects/{project_id}/corpus/{corpus_id}/authors/production",
    response_model=AuthorProductionEnvelope,
)
async def get_author_production(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    """作者年度产出时间线 (热力图: 作者 × 年份)。需 PY 字段; 缺则信封 missing_field。"""
    return await _proxy_envelope(r.get_author_production, corpus_id, project_id)


@app.get(
    "/projects/{project_id}/corpus/{corpus_id}/documents/keyword-trend",
    response_model=KeywordTrendEnvelope,
)
async def get_keyword_trend(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    """关键词历时演变 (themeRiver / 堆叠面积)。需 DE+PY 字段; 缺则信封 missing_field。"""
    return await _proxy_envelope(r.get_keyword_trend, corpus_id, project_id)


@app.get(
    "/projects/{project_id}/corpus/{corpus_id}/documents/cited-refs",
    response_model=CitedRefsEnvelope,
)
async def get_cited_refs(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    """高被引参考文献表 (参考文献 | 次数)。需 CR 字段; 缺则信封 missing_field。"""
    return await _proxy_envelope(r.get_cited_refs, corpus_id, project_id)


# --- A5 高级图② 端点 (返回可用性信封; available:false 也是 HTTP 200) ---

@app.get(
    "/projects/{project_id}/corpus/{corpus_id}/conceptual/thematic",
    response_model=ThematicEnvelope,
)
async def get_thematic(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    """主题战略图 (Callon 中心度×密度 四象限)。需 DE 字段; 缺则信封 missing_field。"""
    return await _proxy_envelope(r.get_thematic, corpus_id, project_id)


@app.get(
    "/projects/{project_id}/corpus/{corpus_id}/conceptual/evolution",
    response_model=EvolutionEnvelope,
)
async def get_evolution(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    """主题演进图 (多周期主题流)。需 DE+PY 且跨度可切≥2 周期; 不足则信封降级。"""
    return await _proxy_envelope(r.get_evolution, corpus_id, project_id)


@app.get(
    "/projects/{project_id}/corpus/{corpus_id}/intellectual/histcite",
    response_model=HistciteEnvelope,
)
async def get_histcite(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    """历史引文图 (时序分层引用脉络)。需 CR 字段; 节点<2 则信封 not_enough_data。"""
    return await _proxy_envelope(r.get_histcite, corpus_id, project_id)


@app.get(
    "/projects/{project_id}/corpus/{corpus_id}/overview/threefield",
    response_model=ThreeFieldEnvelope,
)
async def get_threefield(
    project_id: str, corpus_id: str, r: RClient = Depends(get_r_client)
):
    """三字段 Sankey (作者→关键词→来源)。需 AU+DE+SO 字段; 缺任一则信封 missing_field。"""
    return await _proxy_envelope(r.get_threefield, corpus_id, project_id)


# --- 网络端点 (A5 §4.4: ?limit 默认 top100, 前端滑块客户端切片) ---

@app.get("/projects/{project_id}/corpus/{corpus_id}/conceptual", response_model=NetworkResult)
async def get_conceptual(project_id: str, corpus_id: str, limit: int = 100,
                         r: RClient = Depends(get_r_client)):
    return await _proxy_get(lambda cid: r.get_conceptual(cid, limit), corpus_id, project_id)


@app.get("/projects/{project_id}/corpus/{corpus_id}/intellectual", response_model=NetworkResult)
async def get_intellectual(project_id: str, corpus_id: str, limit: int = 100,
                           r: RClient = Depends(get_r_client)):
    return await _proxy_get(lambda cid: r.get_intellectual(cid, limit), corpus_id, project_id)


@app.get("/projects/{project_id}/corpus/{corpus_id}/social", response_model=SocialResult)
async def get_social(project_id: str, corpus_id: str, limit: int = 100,
                     r: RClient = Depends(get_r_client)):
    return await _proxy_get(lambda cid: r.get_social(cid, limit), corpus_id, project_id)


@app.get("/projects/{project_id}/corpus/{corpus_id}/cite", response_model=CiteResult)
async def get_cite(project_id: str, corpus_id: str, style: str = "apa", limit: int = 200,
                   r: RClient = Depends(get_r_client)):
    if style not in ("gbt7714", "apa", "mla"):
        raise ApiError(400, "VALIDATION_ERROR", "style 仅支持 gbt7714/apa/mla")
    status, body = await r.get_cite(corpus_id, style, limit)
    if status != 200:
        _raise_from_r(status, body)
    body = dict(body or {})
    body["projectId"] = project_id
    return body


@app.post("/projects/{project_id}/prisma", response_model=PrismaResult)
async def prisma(project_id: str, body: PrismaRequest):
    try:
        return build_prisma(body.identified, body.duplicates, body.screened,
                            body.excluded, body.included)
    except ValueError as e:
        raise ApiError(400, "VALIDATION_ERROR", str(e))


@app.post("/projects/{project_id}/corpus/{corpus_id}/report")
async def report(
    project_id: str,
    corpus_id: str,
    request: Request,
    body: ReportOptions = ReportOptions(),
    format: str = "md",
    r: RClient = Depends(get_r_client),
):
    """导出分析报告 (md/html/docx 附件下载)。

    A7: POST + ReportOptions(title/author/sections/prismaCounts/reviewMarkdown), format 走 query。
    docx 走 pandoc; pandoc 缺失 → 503 + 中文文案 (前端据此降级仅显示 md/html)。
    """
    if format not in ("md", "html", "docx"):
        raise ApiError(400, "VALIDATION_ERROR", "format 仅支持 md/html/docx")
    # DOCX 运行时约束: pandoc 不可用直接 503, 不白跑下游分析查询。
    if format == "docx" and not getattr(request.app.state, "pandoc_ok", False):
        raise ApiError(
            503, "PANDOC_UNAVAILABLE",
            "服务端未安装 pandoc, 暂不支持 DOCX 导出, 请改用 Markdown 或 HTML。",
        )

    overview = await _proxy_get(r.get_overview, corpus_id, project_id)
    sources = await _proxy_get(r.get_sources, corpus_id, project_id)
    authors = await _proxy_get(r.get_authors, corpus_id, project_id)
    documents = await _proxy_get(r.get_documents, corpus_id, project_id)

    meta: dict = {"title": body.title, "author": body.author}
    if body.prismaCounts is not None:
        meta["prismaCounts"] = body.prismaCounts.model_dump()
    if body.reviewMarkdown is not None:
        meta["reviewMarkdown"] = body.reviewMarkdown
    # references 章节: 取 APA 引用作为参考文献列表 (失败则降级为高被引代表)。
    if "references" in body.sections:
        status, cite_body = await r.get_cite(corpus_id, "apa", 200)
        if status == 200 and cite_body:
            meta["citations"] = cite_body.get("citations", [])

    try:
        content, media = build_report(
            format, meta, overview, sources, authors, documents,
            sections=body.sections,
        )
    except PandocUnavailable:
        raise ApiError(
            503, "PANDOC_UNAVAILABLE",
            "服务端未安装 pandoc, 暂不支持 DOCX 导出, 请改用 Markdown 或 HTML。",
        )
    except PandocTimeout:
        raise ApiError(503, "PANDOC_TIMEOUT", "DOCX 转换超时, 请稍后重试或改用其他格式。")
    except PandocFailed:
        log.exception("pandoc docx 转换失败")
        raise ApiError(500, "PANDOC_FAILED", "DOCX 生成失败, 请改用 Markdown 或 HTML。")

    ext = "docx" if format == "docx" else format
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="report.{ext}"'},
    )


def _sse(event: str, data: dict, seq: int | None = None) -> str:
    """格式化 SSE 帧。seq 不为 None 时输出 id: 行（供 Last-Event-ID 断点续传）。"""
    id_line = f"id: {seq}\n" if seq is not None else ""
    return f"{id_line}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/projects/{project_id}/corpus/{corpus_id}/review")
async def review(
    project_id: str,
    corpus_id: str,
    body: ReviewRequest,
    request: Request,
    r: RClient = Depends(get_r_client),
):
    """流式生成综述 (SSE)。逐章节流式输出 LLM token, 结束跑引用校验。
    用户自带 LLM key 经 X-LLM-Key 头透传, 不落盘 (沿用 v0.6)。"""
    if body.type not in REVIEW_TYPES:
        raise ApiError(400, "VALIDATION_ERROR", f"未知论型: {body.type}")
    status, rec_body = await r.get_records(corpus_id, settings.review_records_limit)
    if status != 200:
        _raise_from_r(status, rec_body)
    records = (rec_body or {}).get("records", [])
    llm = _llm(request)
    tpl = review_template(body.type)
    ctx = build_review_context(body.topic, records)

    async def gen():
        parts: list[str] = []
        try:
            yield _sse("meta", {"template": tpl["name"],
                                "chapters": [c["title"] for c in tpl["chapters"]],
                                "docCount": len(records)})
            for i, ch in enumerate(tpl["chapters"]):
                yield _sse("chapter", {"index": i, "title": ch["title"]})
                parts.append(f"\n\n## {ch['title']}\n\n")
                async for tok in llm.stream(prompt_review(ctx, tpl, ch)):
                    parts.append(tok)
                    yield _sse("token", {"text": tok})
            cc = check_citations("".join(parts), records)
            yield _sse("citations", {"summary": cc["summary"], "annotated": cc["annotated"]})
            yield _sse("done", {"chapters": len(tpl["chapters"])})
        except LLMError as e:
            yield _sse("error", {"code": "LLM_ERROR", "message": e.message})
        except Exception:
            log.exception("review generation failed")
            yield _sse("error", {"code": "INTERNAL", "message": "综述生成失败"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- 切片6: AI 功能 ----

def _llm_header(request: Request, name: str) -> str | None:
    value = request.headers.get(name)
    return value.strip() if value and value.strip() else None


def _image_value(request: Request, body=None, field: str = "") -> str | None:
    body_value = getattr(body, field, None) if body is not None else None
    header_name = {
        "apiKey": "X-Image-Key",
        "baseUrl": "X-Image-Base-URL",
        "model": "X-Image-Model",
        "size": "X-Image-Size",
    }.get(field, "")
    header_value = request.headers.get(header_name) if header_name else None
    value = body_value or header_value
    return value.strip() if isinstance(value, str) and value.strip() else None


def _image_runtime_options(request: Request, body=None) -> dict[str, str]:
    return {
        "apiKey": _image_value(request, body, "apiKey") or settings.image_api_key,
        "baseUrl": _image_value(request, body, "baseUrl") or settings.image_base_url,
        "model": _image_value(request, body, "model") or settings.image_model,
        "size": _image_value(request, body, "size") or settings.image_size,
    }


def _sciverse_override(request: Request, body=None) -> tuple[str | None, str | None]:
    base_url = getattr(body, "baseUrl", None) if body is not None else None
    base_url = base_url or request.headers.get("X-Sciverse-Base-URL")
    api_token = request.headers.get("X-Sciverse-Token")
    return (
        base_url.strip() if isinstance(base_url, str) and base_url.strip() else None,
        api_token.strip() if isinstance(api_token, str) and api_token.strip() else None,
    )


def _sciverse_client(request: Request, body=None) -> SciverseClient:
    base_url, api_token = _sciverse_override(request, body)
    return SciverseClient(sciverse_config(base_url, api_token))


def _sciverse_run_override(request: Request) -> dict | None:
    base_url, api_token = _sciverse_override(request)
    data: dict[str, str] = {}
    if base_url:
        data["base_url"] = base_url
    if api_token:
        data["api_token"] = api_token
    return data or None


def _llm(request: Request):
    return get_llm_client(
        _llm_header(request, "X-LLM-Key"),
        base_url=_llm_header(request, "X-LLM-Base-URL"),
        model=_llm_header(request, "X-LLM-Model"),
    )


def _llm_override(request: Request):
    return override_from_key(
        _llm_header(request, "X-LLM-Key"),
        base_url=_llm_header(request, "X-LLM-Base-URL"),
        model=_llm_header(request, "X-LLM-Model"),
    )


async def _complete_or_502(llm, messages) -> str:
    try:
        return await llm.complete(messages)
    except LLMError as e:
        raise ApiError(502, "ANALYSIS_FAILED", e.message)


def _dt_iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _image_assets_dir() -> Path:
    root = Path(settings.corpora_dir) / "assets" / "infographics"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _image_asset_url(filename: str) -> str:
    return f"/ai/assets/infographics/{filename}"


def _image_suffix(content_type: str | None = None, url: str | None = None) -> str:
    if content_type == "image/jpeg":
        return ".jpg"
    if content_type == "image/webp":
        return ".webp"
    if content_type == "image/svg+xml":
        return ".svg"
    suffix = Path(url or "").suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg"} else ".png"


def _save_image_asset(data: bytes, suffix: str) -> str:
    filename = f"{uuid.uuid4().hex}{suffix}"
    (_image_assets_dir() / filename).write_bytes(data)
    return _image_asset_url(filename)


async def _cache_remote_image_url(url: str) -> str:
    safe_url = normalize_external_url(url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as client:
        # Never forward the image-model API key to arbitrary asset URLs returned by the provider.
        res = await client.get(safe_url)
    if res.status_code >= 400:
        raise RuntimeError(f"image fetch failed {res.status_code}")
    return _save_image_asset(res.content, _image_suffix(res.headers.get("content-type"), safe_url))


def _fallback_infographic_svg(topic: str, body: str, prompt: str) -> str:
    safe_topic = re.sub(r"[<>&]", "", (topic or "Literature Insight").strip())[:90]
    words = re.findall(r"[\w\u4e00-\u9fff]{2,}", body or prompt)
    highlights = []
    for word in words:
        if word.lower() not in {w.lower() for w in highlights}:
            highlights.append(word[:24])
        if len(highlights) >= 4:
            break
    while len(highlights) < 4:
        highlights.append(["Evidence", "Methods", "Findings", "Future"][len(highlights)])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720">
<rect width="1200" height="720" fill="#f5efe2"/>
<rect x="54" y="52" width="1092" height="616" rx="30" fill="#fffaf0" stroke="#221f1a" stroke-width="2"/>
<circle cx="1044" cy="162" r="92" fill="#d4573b" opacity=".18"/>
<circle cx="189" cy="590" r="118" fill="#d8ad54" opacity=".22"/>
<text x="94" y="126" font-family="Georgia,serif" font-size="48" fill="#201f1b">One Figure Review</text>
<text x="94" y="174" font-family="Arial,sans-serif" font-size="28" fill="#5b554a">{safe_topic}</text>
<g font-family="Arial,sans-serif" font-size="24" fill="#201f1b">
<rect x="94" y="250" width="210" height="106" rx="18" fill="#dfe8d5" stroke="#201f1b"/><text x="128" y="312">{highlights[0]}</text>
<rect x="356" y="250" width="210" height="106" rx="18" fill="#f3d184" stroke="#201f1b"/><text x="390" y="312">{highlights[1]}</text>
<rect x="618" y="250" width="210" height="106" rx="18" fill="#c7d8e8" stroke="#201f1b"/><text x="652" y="312">{highlights[2]}</text>
<rect x="880" y="250" width="210" height="106" rx="18" fill="#e8c1b3" stroke="#201f1b"/><text x="914" y="312">{highlights[3]}</text>
</g>
<path d="M304 303H356M566 303H618M828 303H880" stroke="#201f1b" stroke-width="4"/>
<text x="94" y="462" font-family="Arial,sans-serif" font-size="25" fill="#201f1b">Generated as a deterministic fallback when no image model is configured.</text>
<text x="94" y="512" font-family="Arial,sans-serif" font-size="21" fill="#5b554a">The saved prompt can be regenerated with an OpenAI-compatible image model.</text>
<text x="94" y="594" font-family="Georgia,serif" font-size="30" fill="#201f1b">BiblioCN Data Agent</text>
</svg>"""


def _one_figure_prompt(topic: str, text: str, style: str | None = None) -> str:
    style_text = (style or "academic infographic, editorial, clean, citation-aware").strip()
    source = re.sub(r"\s+", " ", text.strip())[:1800]
    return (
        "Create a single academic infographic for a literature review. "
        "No tiny unreadable text, no fake citations, no logos. "
        f"Topic: {topic.strip() or 'research literature review'}. "
        f"Visual style: {style_text}. "
        "Use a strong hierarchy with four sections: research context, evidence base, main findings, future directions. "
        f"Grounding text: {source}"
    )


def _infographic_prompt_messages(topic: str, context: str, style: str | None = None) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是科研信息图提示词设计师。请根据真实文献综述语料与计量分析结果，"
                "生成可直接交给 OpenAI 兼容生图模型的中文/英文混合提示词。"
                "必须强调一图读懂、结构清晰、不要伪造引用、不要生成小号密集文字。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"研究主题：{topic or '未命名研究主题'}\n"
                f"视觉风格：{style or '学术信息图、编辑设计、清晰层级'}\n\n"
                "请基于以下材料生成一段生图提示词，只输出提示词本身：\n"
                f"{context[:6000]}"
            ),
        },
    ]


async def _generate_infographic_image(options: dict[str, str], prompt: str, topic: str, text: str) -> dict:
    api_key = (options.get("apiKey") or "").strip()
    if not api_key:
        svg = _fallback_infographic_svg(topic, text, prompt)
        url = _save_image_asset(svg.encode("utf-8"), ".svg")
        return {"status": "prompt-only", "prompt": prompt, "url": url, "mimeType": "image/svg+xml"}

    base_url = _normalize_base_url(options.get("baseUrl") or settings.image_base_url)
    payload = {
        "model": options.get("model") or settings.image_model,
        "prompt": prompt,
        "size": options.get("size") or settings.image_size,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            res = await client.post(f"{base_url}/images/generations", json=payload, headers=headers)
        if res.status_code >= 400:
            raise RuntimeError(f"image api returned {res.status_code}: {res.text[:300]}")
        first = (res.json().get("data") or [{}])[0]
        if first.get("url"):
            remote_url = str(first["url"])
            url = await _cache_remote_image_url(remote_url)
            return {"status": "generated", "prompt": prompt, "url": url, "remoteUrl": remote_url, "mimeType": "image/*"}
        if first.get("b64_json"):
            url = _save_image_asset(base64.b64decode(first["b64_json"]), ".png")
            return {"status": "generated", "prompt": prompt, "url": url, "mimeType": "image/png"}
    except Exception as exc:  # noqa: BLE001
        log.warning("infographic image generation failed, fallback to svg: %s", exc)

    svg = _fallback_infographic_svg(topic, text, prompt)
    url = _save_image_asset(svg.encode("utf-8"), ".svg")
    return {"status": "fallback", "prompt": prompt, "url": url, "mimeType": "image/svg+xml"}


def _ai_job_item(job) -> AiJobItem:
    return AiJobItem(
        id=job.id,
        projectId=job.project_id,
        corpusId=job.corpus_id,
        kind=job.kind,
        status=normalize_run_status(job.status),
        request=job.request_json,
        resultText=job.result_text or "",
        annotatedText=job.annotated_text,
        summary=job.summary_json,
        provenanceMap=(job.summary_json or {}).get("provenance_map") or None,
        events=list(job.events_json or []),
        error=job.error,
        createdAt=_dt_iso(job.created_at),
        updatedAt=_dt_iso(job.updated_at),
        completedAt=_dt_iso(job.completed_at),
    )


def _safe_job_request(body: AiJobCreateRequest, request: Request) -> dict:
    data = body.model_dump()
    data["llm"] = {
        "baseUrl": _llm_header(request, "X-LLM-Base-URL"),
        "model": _llm_header(request, "X-LLM-Model"),
        "hasApiKey": bool(_llm_header(request, "X-LLM-Key")),
    }
    image = _image_runtime_options(request)
    data["image"] = {
        "baseUrl": image["baseUrl"],
        "model": image["model"],
        "size": image["size"],
        "hasApiKey": bool(image["apiKey"]),
    }
    return data


async def _job_event(s, job, event: str, data: dict | None = None, **updates):
    payload = {"event": event, "data": data or {}}
    return await ai_job_repo.update_job(s, job, append_event=payload, **updates)


async def _run_ai_job(job_id: int, payload: dict, llm, override, r: RClient):
    async with SessionLocal() as s:
        job = None
        try:
            job = (
                await ai_job_repo.get_job(s, int(payload["projectId"]), job_id)
                if "projectId" in payload else None
            )
            if job is None:
                log.warning(
                    "ai job %s not found for project %s, skip",
                    job_id, payload.get("projectId"),
                )
                return
            job = await _job_event(s, job, "started", status="running")
            kind = payload.get("kind")

            if kind == "review":
                review_type = payload.get("type") or "undergrad"
                topic = (payload.get("topic") or "").strip()
                if review_type not in REVIEW_TYPES or not topic:
                    raise ApiError(400, "VALIDATION_ERROR", "综述任务参数不完整")
                pid_rev = int(payload["projectId"])
                # 优先走 B4 可溯源综述：项目有 OCR 全文 + content_list 结构时，
                # run_review 在精读阶段把每条引用定位到原文页/段/表，产 provenance_map。
                async with SessionLocal() as s2:
                    prov_markdowns, prov_records, _prov_skipped = await load_project_corpus(s2, pid_rev)
                if prov_markdowns:
                    prov_tpl = get_template(review_type)
                    job = await _job_event(
                        s, job, "meta",
                        {"mode": "provenance", "docCount": len(prov_markdowns), "template": review_type},
                    )
                    result = await run_review(
                        topic=topic, paper_markdowns=prov_markdowns, records=prov_records,
                        template=prov_tpl, concurrency=4, override=override,
                    )
                    if result.get("error"):
                        raise ApiError(500, "REVIEW_ERROR", str(result["error"]))
                    # citation 三色与 corpus 模式完全同口径：对最终正文跑 check_citations(与 corpus
                    # 分支同一函数、同一"正文引用 occurrence"口径)，而非从去重/混层的 evidence_refs 统计
                    # (口径不一致会让前端零伪造率分子/分母混口径而失真,codex A2-P1)。此前 provenance 模式
                    # 把 summary 包成 citations 子键→前端读 summary.green 为空,图例无数字(dogfood A2)。
                    # validation_summary 全量保留供 TrustCard/审计(GuardedStream 拦截视角,与三色互补)。
                    _cc = check_citations(result.get("review_md", "") or "", prov_records)
                    summary = {
                        **_cc["summary"],
                        "validation_summary": result.get("validation_summary") or {},
                        "provenance_map": result.get("provenance_map") or {},
                    }
                    await _job_event(
                        s, job, "done",
                        {"mode": "provenance", "evidence": len(result.get("evidence_refs") or [])},
                        status="done", complete=True,
                        result_text=result.get("review_md", ""),
                        summary_json=summary,
                    )
                    return
                # 回退：项目无 OCR 全文（无 structure）→ 旧 corpus 综述（无页/段溯源）
                corpus_id = payload.get("corpusId")
                if not corpus_id:
                    raise ApiError(400, "VALIDATION_ERROR", "项目无可读全文语料，且未提供 corpusId")
                status, rec_body = await r.get_records(corpus_id, settings.review_records_limit)
                if status != 200:
                    raise ApiError(status, "CORPUS_ERROR", str(rec_body))
                records = (rec_body or {}).get("records", [])
                tpl = review_template(review_type)
                ctx = build_review_context(topic, records)
                parts: list[str] = []
                job = await _job_event(
                    s, job, "meta",
                    {"template": tpl["name"], "chapters": [c["title"] for c in tpl["chapters"]], "docCount": len(records)},
                )
                for i, ch in enumerate(tpl["chapters"]):
                    parts.append(f"\n\n## {ch['title']}\n\n")
                    job = await _job_event(s, job, "chapter", {"index": i, "title": ch["title"]}, result_text="".join(parts))
                    async for tok in llm.stream(prompt_review(ctx, tpl, ch)):
                        parts.append(tok)
                        job.result_text = "".join(parts)
                        await s.commit()
                cc = check_citations("".join(parts), records)
                job = await _job_event(
                    s,
                    job,
                    "citations",
                    {"summary": cc["summary"], "annotated": cc["annotated"]},
                    result_text="".join(parts),
                    annotated_text=cc["annotated"],
                    summary_json=cc["summary"],
                )
                await _job_event(s, job, "done", {"chapters": len(tpl["chapters"])}, status="done", complete=True)
                return

            if kind == "chat":
                corpus_id = payload.get("corpusId")
                query = (payload.get("query") or "").strip()
                if not corpus_id or not query:
                    raise ApiError(400, "VALIDATION_ERROR", "对话任务参数不完整")
                status, rec = await r.get_records(corpus_id, settings.review_records_limit)
                if status != 200:
                    raise ApiError(status, "CORPUS_ERROR", str(rec))
                records = (rec or {}).get("records", [])
                ctx = build_review_context("", records)
                history = [
                    {"role": m.get("role"), "content": m.get("content")}
                    for m in (payload.get("history") or [])
                    if isinstance(m, dict)
                ]
                parts: list[str] = []
                async for tok in llm.stream(prompt_chat(history, ctx, query)):
                    parts.append(tok)
                    job.result_text = "".join(parts)
                    await s.commit()
                await _job_event(s, job, "done", {}, status="done", result_text="".join(parts), complete=True)
                return

            if kind == "infographic_prompt":
                text = (payload.get("text") or "").strip()
                topic = (payload.get("topic") or "").strip()
                corpus_id = payload.get("corpusId")
                context_parts = [text]
                if corpus_id:
                    status, rec = await r.get_records(corpus_id, min(settings.review_records_limit, 30))
                    if status == 200:
                        records = (rec or {}).get("records", [])
                        context_parts.append(
                            f"\n[文献语料]\n{json.dumps(build_review_context(topic, records), ensure_ascii=False)[:2000]}"
                        )
                    for label, getter in (
                        ("overview", r.get_overview),
                        ("documents", r.get_documents),
                        ("sources", r.get_sources),
                    ):
                        try:
                            s_code, data = await getter(corpus_id)
                            if s_code == 200:
                                context_parts.append(f"\n[{label}]\n{json.dumps(data, ensure_ascii=False)[:1600]}")
                        except Exception as exc:  # noqa: BLE001
                            log.info("skip infographic %s context: %s", label, exc)
                context = "\n\n".join(part for part in context_parts if part)
                if not context.strip():
                    raise ApiError(400, "VALIDATION_ERROR", "请先输入综述文本，或在分析语料就绪后生成提示词")
                output = await llm.complete(
                    _infographic_prompt_messages(topic, context, payload.get("style")),
                    temperature=0.4,
                    max_tokens=900,
                )
                if not output.strip():
                    output = _one_figure_prompt(topic, context, payload.get("style"))
                await _job_event(
                    s,
                    job,
                    "done",
                    {"source": "llm", "hasCorpus": bool(corpus_id)},
                    status="done",
                    result_text=output.strip(),
                    complete=True,
                )
                return

            if kind == "infographic_image":
                image_prompt = (payload.get("imagePrompt") or payload.get("text") or "").strip()
                topic = (payload.get("topic") or "").strip()
                if not image_prompt:
                    raise ApiError(400, "VALIDATION_ERROR", "请先生成或填写生图提示词")
                job = await _job_event(s, job, "image_start", {"model": (payload.get("_image") or {}).get("model")})
                image = await _generate_infographic_image(payload.get("_image") or {}, image_prompt, topic, image_prompt)
                result_text = f"# 一图读懂\n\n![一图读懂]({image['url']})\n\n## 生图提示词\n\n{image_prompt}\n"
                await _job_event(
                    s,
                    job,
                    "done",
                    {"imageUrl": image["url"], "status": image["status"]},
                    status="done",
                    result_text=result_text,
                    summary_json=image,
                    complete=True,
                )
                return

            text = (payload.get("text") or "").strip()
            if not text:
                raise ApiError(400, "VALIDATION_ERROR", "文本不能为空")
            if kind == "translate":
                direction = payload.get("direction") or "en2zh"
                if direction not in TRANSLATE_DIRECTIONS:
                    raise ApiError(400, "VALIDATION_ERROR", "direction 仅支持 en2zh/zh2en")
                output = await llm.complete(prompt_translate(text, direction))
            elif kind == "rewrite":
                action = payload.get("action") or "compress"
                if action not in REWRITE_ACTIONS:
                    raise ApiError(400, "VALIDATION_ERROR", "action 仅支持 counter/compress/expand/casual")
                output = await llm.complete(prompt_rewrite(text, action))
            elif kind == "summary":
                output = await llm.complete(prompt_summary("", text))
            else:
                raise ApiError(400, "VALIDATION_ERROR", f"未知 AI 任务类型: {kind}")
            await _job_event(s, job, "done", {}, status="done", result_text=output, complete=True)
        except LLMError as e:
            if job is not None:
                await _job_event(s, job, "error", {"code": "LLM_ERROR", "message": e.message}, status="failed", error=e.message, complete=True)
        except ApiError as e:
            if job is not None:
                await _job_event(s, job, "error", {"code": e.code, "message": e.message}, status="failed", error=e.message, complete=True)
        except Exception as e:
            log.exception("ai job failed")
            if job is not None:
                await _job_event(s, job, "error", {"code": "INTERNAL", "message": "AI 任务执行失败"}, status="failed", error=str(e), complete=True)


@app.post("/projects/{pid:int}/ai/jobs", response_model=AiJobItem, status_code=202)
async def create_ai_job(
    pid: int,
    body: AiJobCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    r: RClient = Depends(get_r_client),
    s=Depends(get_session),
):
    """创建可恢复的 AI 生成任务。API key 只透传给本次后台任务，不落库。"""
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")
    req = _safe_job_request(body, request)
    req["projectId"] = pid
    runtime_req = dict(req)
    runtime_req["_image"] = _image_runtime_options(request)
    job = await ai_job_repo.create_job(
        s,
        project_id=pid,
        kind=body.kind,
        corpus_id=body.corpusId,
        request_json=req,
    )
    background_tasks.add_task(
        _run_ai_job, job.id, runtime_req, _llm(request), _llm_override(request), r
    )
    return _ai_job_item(job)


@app.get("/projects/{pid:int}/ai/jobs", response_model=dict)
async def list_ai_jobs(
    pid: int,
    kind: str | None = None,
    corpusId: str | None = None,
    limit: int = 20,
    s=Depends(get_session),
):
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")
    rows = await ai_job_repo.list_jobs(s, project_id=pid, kind=kind, corpus_id=corpusId, limit=limit)
    return {"jobs": [_ai_job_item(j) for j in rows]}


@app.get("/projects/{pid:int}/ai/jobs/{job_id:int}", response_model=AiJobItem)
async def get_ai_job(pid: int, job_id: int, s=Depends(get_session)):
    job = await ai_job_repo.get_job(s, pid, job_id)
    if job is None:
        raise ApiError(404, "AI_JOB_NOT_FOUND", f"AI 任务 {job_id} 不存在")
    return _ai_job_item(job)


@app.post("/ai/ping")
async def ai_ping(request: Request):
    """真实 LLM 连通性测试。只通过请求头接收 key/base_url/model，不落库不回显 key。"""
    if not _llm_header(request, "X-LLM-Key"):
        raise ApiError(400, "LLM_KEY_REQUIRED", "请先提供 LLM API Key")
    try:
        text = await _llm(request).complete(
            [{"role": "user", "content": "Respond with exactly: pong"}],
            temperature=0,
            max_tokens=8,
        )
    except LLMError as e:
        raise ApiError(502, "LLM_PING_FAILED", e.message)
    except Exception:
        log.exception("llm ping failed")
        raise ApiError(502, "LLM_PING_FAILED", "LLM 测试请求失败")
    return {
        "ok": True,
        "model": _llm_header(request, "X-LLM-Model") or "deepseek-chat",
        "baseUrl": _llm_header(request, "X-LLM-Base-URL") or settings.deepseek_base_url,
        "content": text,
    }


@app.post("/ai/image/ping", response_model=ImagePingResult)
async def image_ping(body: ImageSettingsPayload, request: Request):
    options = _image_runtime_options(request, body)
    if not options["apiKey"].strip():
        raise ApiError(400, "IMAGE_KEY_REQUIRED", "请先提供生图 API Key")
    base_url = _normalize_base_url(options["baseUrl"])
    payload = {
        "model": options["model"],
        "prompt": "A clean academic infographic icon showing a literature review workflow, no text.",
        "size": options["size"],
    }
    headers = {"Authorization": f"Bearer {options['apiKey']}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            res = await client.post(f"{base_url}/images/generations", json=payload, headers=headers)
        if res.status_code >= 400:
            raise ApiError(502, "IMAGE_PING_FAILED", f"生图请求失败 {res.status_code}: {res.text[:300]}")
        first = (res.json().get("data") or [{}])[0]
        if not (first.get("url") or first.get("b64_json")):
            raise ApiError(502, "IMAGE_PING_FAILED", "生图接口未返回图片数据")
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(502, "IMAGE_PING_FAILED", f"生图测试请求失败: {exc}") from exc
    return ImagePingResult(
        ok=True,
        model=options["model"],
        baseUrl=base_url,
        size=options["size"],
        detail="生图接口已返回图片数据",
    )


@app.get("/ai/assets/infographics/{filename}")
async def get_infographic_asset(filename: str):
    safe = Path(filename).name
    path = _image_assets_dir() / safe
    if not path.exists() or not path.is_file():
        raise ApiError(404, "IMAGE_ASSET_NOT_FOUND", "一图读懂图片不存在")
    media = {
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.post("/sciverse/ping", response_model=SciversePingResult, tags=["sciverse"])
async def sciverse_ping(body: SciverseSettingsPayload, request: Request):
    """真实 Sciverse 连通性测试。只测试 meta-search，Token 不落库不回显。"""
    client = _sciverse_client(request, body)
    meta = await client.meta_search(query="bibliometrics", page_size=1)
    results = meta.get("results") or []
    cfg = sciverse_config(*_sciverse_override(request, body))
    return SciversePingResult(ok=True, baseUrl=cfg.base_url, resultCount=len(results))


@app.post("/sciverse/meta-search", response_model=SciverseMetaSearchResult, tags=["sciverse"])
async def sciverse_meta_search(body: SciverseMetaSearchRequest, request: Request):
    """Sciverse 元数据检索：返回候选卡，不直接写入数据库。"""
    if not body.query and not body.filters:
        raise ApiError(400, "VALIDATION_ERROR", "query 和 filters 至少提供一个")
    meta = await _sciverse_client(request, body).meta_search(
        query=body.query,
        filters=body.filters or None,
        sort=body.sort or None,
        fields=body.fields or [
            "title",
            "doi",
            "abstract",
            "author",
            "keywords",
            "publication_published_year",
            "publication_published_date",
            "publication_venue_name_unified",
            "citation_count",
            "reference_count",
            "doc_id",
            "unique_id",
        ],
        page=body.page,
        page_size=body.pageSize,
        cursor=body.cursor,
        freshness_boost=body.freshnessBoost,
    )
    candidates = [
        normalize_meta_result(row)
        for row in (meta.get("results") or [])
        if isinstance(row, dict) and (row.get("title") or "").strip()
    ]
    return SciverseMetaSearchResult(
        candidates=candidates,
        partial=bool(meta.get("partial")),
        partialReason=meta.get("partialReason"),
        totalCount=meta.get("total_count"),
        page=meta.get("page"),
        pageSize=meta.get("page_size"),
        totalPages=meta.get("total_pages"),
        nextCursor=meta.get("next_cursor"),
        searchTimeMs=meta.get("search_time_ms"),
    )


@app.post("/sciverse/agentic-search", response_model=SciverseAgenticSearchResult, tags=["sciverse"])
async def sciverse_agentic_search(body: SciverseAgenticSearchRequest, request: Request):
    """Sciverse 片段检索：用于 RAG/证据发现，不直接创建 Paper。"""
    data = await _sciverse_client(request, body).agentic_search(
        body.query,
        top_k=body.topK,
        sub_queries=body.subQueries,
    )
    hits = [
        normalize_agentic_hit(row)
        for row in (data.get("hits") or [])
        if isinstance(row, dict)
    ]
    return SciverseAgenticSearchResult(hits=hits)


@app.post("/projects/{project_id}/ai/translate", response_model=TextResult)
async def ai_translate(project_id: str, body: TranslateRequest, request: Request):
    if body.direction not in TRANSLATE_DIRECTIONS:
        raise ApiError(400, "VALIDATION_ERROR", "direction 仅支持 en2zh/zh2en")
    text = await _complete_or_502(_llm(request), prompt_translate(body.text, body.direction))
    return {"text": text}


@app.post("/projects/{project_id}/ai/rewrite", response_model=TextResult)
async def ai_rewrite(project_id: str, body: RewriteRequest, request: Request):
    if body.action not in REWRITE_ACTIONS:
        raise ApiError(400, "VALIDATION_ERROR", "action 仅支持 counter/compress/expand/casual")
    text = await _complete_or_502(_llm(request), prompt_rewrite(body.text, body.action))
    return {"text": text}


@app.post("/projects/{project_id}/ai/summary", response_model=TextResult)
async def ai_summary(project_id: str, body: SummaryRequest, request: Request):
    text = await _complete_or_502(_llm(request), prompt_summary("", body.text))
    return {"text": text}


def _parse_screen(raw: str) -> tuple[int | None, str]:
    if not raw:
        return None, "(无输出)"
    m = re.search(r"\{.*\}", raw, re.DOTALL)  # 提取首个 JSON object (容忍 fence/前后解释)
    blob = m.group(0) if m else raw
    try:
        obj = json.loads(blob)
        rel = obj.get("relevance")
        if isinstance(rel, str):
            try:
                rel = int(float(rel.strip()))
            except ValueError:
                rel = None
        rel = max(0, min(10, int(rel))) if isinstance(rel, (int, float)) and not isinstance(rel, bool) else None
        reason = obj.get("reason", "")
        reason = str(reason)[:200] if isinstance(reason, (str, int, float)) else "(理由格式异常)"
        return rel, reason or "(无理由)"
    except Exception:
        return None, raw[:200]


@app.post("/projects/{project_id}/corpus/{corpus_id}/ai/screen", response_model=ScreenResult)
async def ai_screen(
    project_id: str, corpus_id: str, body: ScreenRequest, request: Request,
    r: RClient = Depends(get_r_client),
):
    status, rec = await r.get_records(corpus_id, body.limit)
    if status != 200:
        _raise_from_r(status, rec)
    records = (rec or {}).get("records", [])[: body.limit]
    llm = _llm(request)
    sem = asyncio.Semaphore(5)  # 限并发, 防 N 篇串行打爆网关/worker (Codex slice6-P1)

    async def _one(rd: dict) -> dict:
        async with sem:
            try:
                raw = await llm.complete(
                    prompt_screen(body.topic, rd.get("title", ""),
                                  rd.get("abstract", ""), rd.get("keywords", "")))
            except LLMError:
                return {"idx": rd.get("idx", 0), "relevance": None, "reason": "评估失败"}
        rel, reason = _parse_screen(raw)
        return {"idx": rd.get("idx", 0), "relevance": rel, "reason": reason}

    results = await asyncio.gather(*[_one(rd) for rd in records])
    return {"results": list(results)}


@app.post("/projects/{project_id}/corpus/{corpus_id}/ai/chat")
async def ai_chat(
    project_id: str, corpus_id: str, body: ChatRequest, request: Request,
    r: RClient = Depends(get_r_client),
):
    status, rec = await r.get_records(corpus_id, settings.review_records_limit)
    if status != 200:
        _raise_from_r(status, rec)
    records = (rec or {}).get("records", [])
    llm = _llm(request)
    ctx = build_review_context("", records)
    history = [{"role": m.role, "content": m.content} for m in body.history]

    async def gen():
        try:
            async for tok in llm.stream(prompt_chat(history, ctx, body.query)):
                yield _sse("token", {"text": tok})
            yield _sse("done", {})
        except LLMError as e:
            yield _sse("error", {"code": "LLM_ERROR", "message": e.message})
        except Exception:
            log.exception("chat failed")
            yield _sse("error", {"code": "INTERNAL", "message": "对话失败"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- P1-7: 领域 REST API（项目/文献/纳排）---- int pid 与旧 str project_id 端点共存

@app.get("/projects", response_model=dict)
async def list_projects_endpoint(s=Depends(get_session), user=Depends(get_current_user)):
    """列出当前登录用户的项目。"""
    items = await project_svc.list_projects_dto(s, owner_id=user.id)
    return {"projects": items}


@app.post("/projects", response_model=ProjectRef, status_code=201)
async def create_project_endpoint(
    body: ProjectCreateRequest, s=Depends(get_session), user=Depends(get_current_user),
):
    """创建新项目（归属当前登录用户）。"""
    dto = await project_svc.create_project_dto(
        s,
        name=body.name,
        research_question=body.researchQuestion,
        description=body.description,
        owner_id=user.id,
    )
    return dto


@app.get("/projects/{pid:int}", response_model=ProjectDetail)
async def get_project_endpoint(pid: int, s=Depends(get_session)):
    """取项目详情（含 paperCount/includedCount）。"""
    dto = await project_svc.get_project_dto(s, pid)
    if dto is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")
    return dto


@app.post(
    "/projects/{pid:int}/corpus/materialize",
    response_model=CorpusMaterializeResponse,
    summary="物化项目 included 论文为可分析语料（M2）",
    description=(
        "从项目当前 included 论文集合构建冻结语料快照，并调 R /parse-from-records 建库。\n\n"
        "幂等：相同 included 集合（content_hash 一致）命中已有 ready corpus 直接复用，不重复调 R。\n\n"
        "included 为空时返回 422 EMPTY_INCLUDED 错误。"
    ),
    tags=["corpus"],
)
async def materialize_corpus_endpoint(
    pid: int,
    s=Depends(get_session),
    r: RClient = Depends(get_r_client),
):
    """M2: 物化项目 included 论文 → corpus 快照 → 调 R 建库 → 返回 corpus 元数据。

    复用 corpus_repo.build_corpus_snapshot（幂等快照） + corpus_repo.get_corpus_records
    + r_client.parse_from_records + corpus_repo.mark_ready/mark_failed，
    与 CorpusTool._build 逻辑一致但作为 REST 端点暴露给前端。
    """
    # 1. 校验 project 存在
    from .repositories.project import get_project as _get_proj
    if await _get_proj(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")

    # 2. 构建/复用快照（幂等：相同 content_hash 返回已有 Corpus 行）
    corpus = await corpus_repo.build_corpus_snapshot(s, pid)
    corpus_id = corpus.id

    # 3. 若已 ready 直接复用（不重复调 R）
    if corpus.status == "ready" and corpus.r_corpus_id:
        return CorpusMaterializeResponse(
            corpusId=corpus_id,
            rCorpusId=corpus.r_corpus_id,
            status="ready",
            documentCount=corpus.document_count,
            contentHash=corpus.content_hash,
        )

    # 4. 取 included 题录
    records = await corpus_repo.get_corpus_records(s, corpus_id)

    if not records:
        # 空 included 集合：标 failed 并告知调用方
        reason = f"项目 {pid} 没有 included 论文，无法构建语料"
        await corpus_repo.mark_failed(s, corpus_id, reason)
        raise ApiError(
            422,
            "EMPTY_INCLUDED",
            reason,
        )

    # 5. 调 R /parse-from-records
    try:
        status_code, body = await r.parse_from_records(records)
    except ApiError as exc:
        reason = f"R 服务不可达: {exc.message}"
        await corpus_repo.mark_failed(s, corpus_id, reason)
        raise ApiError(502, "R_SERVICE_UNAVAILABLE", reason) from exc
    except Exception as exc:
        reason = f"R 服务不可达: {exc}"
        await corpus_repo.mark_failed(s, corpus_id, reason)
        raise ApiError(502, "R_SERVICE_UNAVAILABLE", reason) from exc

    if status_code >= 400 or (body or {}).get("status") == "failed":
        code = (body or {}).get("code", "R_FAILED")
        msg = (body or {}).get("error") or (body or {}).get("message", f"R 返回 {status_code}")
        reason = f"R 建库失败: {msg}"
        await corpus_repo.mark_failed(s, corpus_id, reason)
        raise ApiError(502, code, reason)

    # 6. 写回 r_corpus_id + status=ready
    # codex M2-P2#2: R 返回 200 但缺 corpusId 时, 不能标 ready(否则 activeCorpus
    # stale/StageBar 显示就绪, 但 M3 分析拿不到有效 rCorpusId)。校验非空, 否则 failed+502。
    r_corpus_id = (body or {}).get("corpusId", "")
    if not r_corpus_id:
        reason = "R 建库成功但未返回 corpusId, 无法用于分析"
        await corpus_repo.mark_failed(s, corpus_id, reason)
        raise ApiError(502, "R_INVALID_RESPONSE", reason)
    doc_count = int((body or {}).get("documentCount") or len(records))
    # codex M2-P2#1(已知限制): 单用户场景下并发 materialize 同一 content_hash 极少;
    # 真正互斥需对 (project_id, content_hash) 行锁/parsing 态 CAS。当前依赖 build_corpus_snapshot
    # 的唯一约束保证只有一行 corpus, mark_ready 幂等; 若并发会重复调 R 一次, 末次写回覆盖(可接受)。
    corpus = await corpus_repo.mark_ready(s, corpus_id, r_corpus_id, doc_count)

    return CorpusMaterializeResponse(
        corpusId=corpus_id,
        rCorpusId=corpus.r_corpus_id,
        status="ready",
        documentCount=corpus.document_count,
        contentHash=corpus.content_hash,
    )


@app.get("/library/stats", response_model=LibraryStats, tags=["library"])
async def library_stats_endpoint(s=Depends(get_session)):
    """全库统计：totalPapers / withMetadata / withPdf / ocr breakdown。"""
    return await lib_repo.compute_library_stats(s)


@app.get("/projects/{pid:int}/library/stats", response_model=ProjectLibraryStats, tags=["library"])
async def project_library_stats_endpoint(pid: int, s=Depends(get_session)):
    """项目作用域统计：projectPapers / inclusion breakdown / withMetadata / withPdf / ocr。"""
    dto = await project_svc.get_project_dto(s, pid)
    if dto is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")
    return await lib_repo.compute_library_stats(s, project_id=pid)


@app.get("/projects/{pid:int}/papers", response_model=dict)
async def list_project_papers_endpoint(pid: int, s=Depends(get_session)):
    """列出项目的所有文献（含纳排状态 + 附件/OCR/元数据状态字段）。"""
    items = await project_svc.list_project_papers_dto(s, pid)
    return {"papers": items}


@app.get("/projects/{pid:int}/papers/{paperId:int}", response_model=PaperDetail)
async def get_paper_detail_endpoint(pid: int, paperId: int, s=Depends(get_session)):
    """取项目内单篇文献详情（含 tags/notes/纳排状态）。"""
    return await project_svc.get_paper_detail_dto(s, pid, paperId)


# MinerU 解析全文预览上限（字符）。超出截断并置 truncated=true。
_MAX_MARKDOWN_CHARS = 60000


@app.get("/projects/{pid:int}/papers/{paperId:int}/markdown")
async def get_paper_markdown_endpoint(pid: int, paperId: int, s=Depends(get_session)):
    """取某文献的 MinerU 解析全文（Markdown）——赛道二「文档处理」能力的 UI 可见入口。

    访问控制同 get_paper_detail：文献须关联到该项目，否则 404。读盘失败 / 无解析全文
    一律返回 available=false（不抛 500），由前端展示「暂无解析全文」。
    """
    from sqlalchemy import select
    from .models import Attachment

    pp = await project_repo.find_project_paper(s, pid, paperId)
    if pp is None:
        raise ApiError(404, "PROJECT_PAPER_NOT_FOUND",
                       f"文献 {paperId} 未关联到项目 {pid}")

    # 取最新且有 markdown_path 的 attachment（与 review/load 选择口径一致：id desc + 优先有 md）
    rows = (
        await s.execute(
            select(Attachment)
            .where(Attachment.paper_id == paperId)
            .order_by(Attachment.id.desc())
        )
    ).scalars().all()
    att = next((a for a in rows if a.markdown_path), None)

    empty = {"available": False, "markdown": "", "length": 0, "truncated": False, "sha256": None}
    if att is None or not att.markdown_path:
        return empty
    try:
        # 路径约束（codex P1，防任意文件读取）：resolve() 跟随符号链接后，要求
        #   ① 父目录名为 "fulltext"（全文存储约定 <corpora>/fulltext/<sha>.md）
        #   ② 文件名恰为 "<att.sha256>.md"（与本附件 sha 绑定）
        # 任一不满足即拒读。这样即使 markdown_path 被污染为绝对路径/../ /symlink→/etc/passwd，
        # 也因文件名/父目录不符而拒绝。不强约束到某个 corpora_dir（历史数据存于不同根目录）。
        if not att.sha256:
            return empty
        md_file = Path(att.markdown_path).resolve()
        allowed_parent = (
            md_file.parent.name == "fulltext"
            or (
                md_file.parent.name == str(paperId)
                and md_file.parent.parent.name == "sciverse"
            )
        )
        if not md_file.is_file() or not allowed_parent or md_file.name != f"{att.sha256}.md":
            return empty
        text = md_file.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — 读盘失败按「无解析全文」处理，不 500
        return empty

    length = len(text)
    truncated = length > _MAX_MARKDOWN_CHARS
    md = text[:_MAX_MARKDOWN_CHARS] if truncated else text
    return {
        "available": bool(md.strip()),
        "markdown": md,
        "length": length,
        "truncated": truncated,
        "sha256": att.sha256,
    }


@app.get("/projects/{pid:int}/papers/{paperId:int}/structure", response_model=StructureResponse)
async def get_paper_structure_endpoint(pid: int, paperId: int, s=Depends(get_session)):
    """取某文献的结构化溯源数据（块视图 + 表格网格 + 页/sha/坐标空间元数据）。

    访问控制同 get_paper_markdown：文献须关联到该项目，否则 404；
    无任一附件落有 DocumentStructure（即未做结构化解析）→ 404（零伪造，不返回空壳）。
    """
    from sqlalchemy import select

    from .models import Attachment, DocumentStructure
    from .structure.blocks import content_list_to_blocks
    from .structure.tables import content_list_to_tables

    pp = await project_repo.find_project_paper(s, pid, paperId)
    if pp is None:
        raise ApiError(404, "PROJECT_PAPER_NOT_FOUND",
                       f"文献 {paperId} 未关联到项目 {pid}")

    # 取该文献的附件（新→旧），用第一个落有 DocumentStructure 的附件。
    atts = (
        await s.execute(
            select(Attachment)
            .where(Attachment.paper_id == paperId)
            .order_by(Attachment.id.desc())
        )
    ).scalars().all()
    att = None
    ds = None
    for candidate in atts:
        ds = (
            await s.execute(
                select(DocumentStructure)
                .where(DocumentStructure.attachment_id == candidate.id)
            )
        ).scalar_one_or_none()
        if ds is not None:
            att = candidate
            break

    if ds is None or att is None:
        raise ApiError(404, "STRUCTURE_NOT_FOUND",
                       f"文献 {paperId} 无结构化解析数据")

    return StructureResponse(
        paper_id=paperId,
        attachment_id=att.id,
        page_count=ds.page_count,
        blocks=content_list_to_blocks(
            ds.content_list or [], ds.page_map or {}, ds.block_line_ranges or {}),
        tables=content_list_to_tables(ds.content_list or [], ds.page_map or {}),
        has_bbox=ds.has_bbox,
        markdown_sha256=ds.markdown_sha256,
        schema_version=ds.schema_version,
        source_pdf_sha256=ds.source_pdf_sha256,
        bbox_coord_space=ds.bbox_coord_space,
        page_width=ds.page_width,
        page_height=ds.page_height,
        rotation=ds.rotation,
    )


@app.get("/projects/{pid:int}/quality-report")
async def get_quality_report_endpoint(pid: int, s=Depends(get_session)):
    """B5: 项目语料的轻量质检报告（确定性、非 LLM）。

    扫描缺失元数据 / 重复题录 / 未解析(OCR) / 已解析未抽取，返回
    {"total", "issues", "by_type"}。空/未知项目返回 total=0。
    """
    from .services.quality_check import build_quality_report

    return await build_quality_report(s, pid)


@app.post(
    "/projects/{pid:int}/papers/{paperId:int}/sciverse/content",
    response_model=SciverseFetchContentResult,
    tags=["sciverse", "library"],
)
async def fetch_sciverse_content_endpoint(
    pid: int,
    paperId: int,
    body: SciverseFetchContentRequest,
    request: Request,
    s=Depends(get_session),
):
    """按 Sciverse doc_id 拉取全文文本，保存为 markdown attachment。"""
    saved = await fetch_and_store_sciverse_content(
        s,
        project_id=pid,
        paper_id=paperId,
        client=_sciverse_client(request, body),
        doc_id=body.docId,
        max_chars=body.maxChars,
    )
    return SciverseFetchContentResult(
        paperId=saved.paper_id,
        docId=saved.doc_id,
        attachmentId=saved.attachment_id,
        chars=saved.chars,
        sha256=saved.sha256,
    )


@app.post(
    "/projects/{pid:int}/papers/fulltext:backfill",
    response_model=SciverseBackfillFulltextResult,
    tags=["sciverse", "library"],
)
async def backfill_sciverse_fulltext_endpoint(
    pid: int,
    body: SciverseBackfillFulltextRequest,
    request: Request,
    s=Depends(get_session),
):
    """批量为项目内 Sciverse doc_id 文献补全文。"""
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")

    client = _sciverse_client(request, body)
    candidates = await select_sciverse_backfill_candidates(
        s,
        project_id=pid,
        paper_ids=body.paperIds,
        exclude_paper_ids=body.excludePaperIds,
    )
    total = len(candidates)
    max_papers = max(1, int(body.maxPapers or 50))
    targets = candidates[:max_papers]
    remaining = max(0, total - len(targets))
    # 候选已物化；释放读事务再进入慢网络拉取，避免长时间占用连接池（codex 复核 P2）
    await s.rollback()

    semaphore = asyncio.Semaphore(4)

    async def _fetch(candidate):
        async with semaphore:
            markdown = await fetch_sciverse_markdown(client, candidate.doc_id)
            return candidate, markdown

    fetched_payloads = await asyncio.gather(
        *[_fetch(candidate) for candidate in targets],
        return_exceptions=True,
    )

    fetched = 0
    failed: list[SciverseBackfillFailedItem] = []
    for item in fetched_payloads:
        if isinstance(item, Exception):
            # 保留 paperId：asyncio.gather 的异常本身不携带入参，按同序回填。
            idx = len(failed) + fetched
            paper_id = targets[idx].paper_id if idx < len(targets) else 0
            reason = item.message if isinstance(item, ApiError) else str(item)
            failed.append(SciverseBackfillFailedItem(paperId=paper_id, reason=reason))
            continue
        candidate, markdown = item
        try:
            await store_sciverse_markdown(
                s,
                paper_id=candidate.paper_id,
                doc_id=candidate.doc_id,
                markdown=markdown,
            )
            fetched += 1
        except Exception as exc:  # noqa: BLE001
            try:
                await s.rollback()
            except Exception:
                pass
            reason = exc.message if isinstance(exc, ApiError) else str(exc)
            failed.append(SciverseBackfillFailedItem(paperId=candidate.paper_id, reason=reason))

    return SciverseBackfillFulltextResult(
        total=total,
        fetched=fetched,
        failed=failed,
        skipped=remaining,
        remaining=remaining,
    )


@app.patch("/projects/{pid:int}/papers/{paperId:int}", response_model=ProjectPaperItem)
async def patch_inclusion_endpoint(
    pid: int, paperId: int, body: InclusionPatchRequest, s=Depends(get_session)
):
    """更新文献纳排状态。"""
    return await project_svc.update_inclusion_dto(
        s,
        project_id=pid,
        paper_id=paperId,
        status=body.inclusionStatus,
        reason=body.exclusionReason,
        score=body.screeningScore,
    )


# ---- M1: 文献导入端点 ----

_VALID_INCLUSION_STATUSES = frozenset({"candidate", "included", "excluded", "maybe"})
_MAX_IMPORT_BYTES = 200 * 1024 * 1024      # 压缩包/单 PDF 上传上限：200 MB
# P1-2 ZIP 炸弹防御：解压层面的限制
_ZIP_MAX_ENTRY_BYTES = 100 * 1024 * 1024   # 单个 entry 解压后上限：100 MB
_ZIP_MAX_TOTAL_BYTES = 500 * 1024 * 1024   # 全 ZIP 解压总大小上限：500 MB
_ZIP_MAX_PDF_COUNT   = 500                 # 单 ZIP 最多提取 PDF 数量


@app.post(
    "/projects/{pid:int}/papers/import",
    response_model=PapersImportResponse,
    status_code=200,
    summary="导入 PDF/ZIP 到项目文献库（幂等）",
    description=(
        "multipart/form-data 上传一个或多个 PDF，或一个包含 PDF 的 ZIP。\n\n"
        "流程：解压(若 ZIP) → 逐篇 ingest_pdfs（已有 sha256 缓存直接复用，不重复 MinerU）"
        " → add_paper（dedup） → add_paper_to_project（默认 candidate）。\n\n"
        "幂等：同一 PDF 重复导入（sha256/dedup_key 命中）→ 跳过，不报错，paperIds 仍含该 id。\n\n"
        "refs 文本（纯 DOI/题录）：本期暂不支持，若需要请使用 "
        "POST /projects/{projectId}/corpus/from-refs 端点。"
    ),
    tags=["library"],
)
async def import_papers_endpoint(
    pid: int,
    request: Request,
    files: List[UploadFile],
    default_status: str = Form(default="candidate"),
    s=Depends(get_session),
):
    """导入 PDF/ZIP 到指定项目，返回 {imported, skipped, failed, paperIds}。"""
    # 1. 校验 project 存在
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")

    # 2. 校验 default_status
    if default_status not in _VALID_INCLUSION_STATUSES:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            f"default_status 仅支持: {', '.join(sorted(_VALID_INCLUSION_STATUSES))}",
        )

    # 3. 收集并解压所有上传文件，提取 PDF 列表
    if not files:
        raise ApiError(400, "VALIDATION_ERROR", "至少需要上传一个文件")

    failed_items: list[ImportFailedItem] = []

    with tempfile.TemporaryDirectory(prefix="bibliocn_import_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        pdf_paths: list[Path] = []  # 待 ingest 的 PDF 路径列表

        for upload in files:
            filename = upload.filename or "upload"
            try:
                content = await upload.read()
            except Exception as exc:
                failed_items.append(ImportFailedItem(
                    name=filename,
                    reason=f"读取上传文件失败: {exc}",
                ))
                continue

            if len(content) > _MAX_IMPORT_BYTES:
                failed_items.append(ImportFailedItem(
                    name=filename,  # 此处 filename 仅用于错误报告，safe_basename 在后面
                    reason="文件超过 200MB 上限",
                ))
                continue

            if not content:
                failed_items.append(ImportFailedItem(
                    name=filename,
                    reason="空文件",
                ))
                continue

            # P1-1：只取客户端文件名的"基名"（去掉目录部分），防路径穿越。
            safe_basename = Path(filename).name or "upload"
            fname_lower = safe_basename.lower()

            if fname_lower.endswith(".zip"):
                # 解压 ZIP，提取其中 PDF
                zip_dest = tmp_path / f"zip_{uuid.uuid4().hex}"
                zip_dest.mkdir(exist_ok=True)
                # P1-1：ZIP 文件本身也用 uuid 命名，避免客户端文件名影响路径
                zip_bytes_path = tmp_path / f"{uuid.uuid4().hex}.zip"
                zip_bytes_path.write_bytes(content)
                try:
                    with zipfile.ZipFile(zip_bytes_path, "r") as zf:
                        # P1-2：解压前遍历 ZipInfo，检验单 entry 大小、总大小、PDF 数量上限
                        total_uncompressed = 0
                        pdf_infos = []
                        for info in zf.infolist():
                            if not info.filename.lower().endswith(".pdf"):
                                continue
                            # 单 entry 上限
                            if info.file_size > _ZIP_MAX_ENTRY_BYTES:
                                failed_items.append(ImportFailedItem(
                                    name=Path(info.filename).name or info.filename,
                                    reason=(
                                        f"ZIP 内 entry 解压后超过 "
                                        f"{_ZIP_MAX_ENTRY_BYTES // 1024 // 1024} MB 上限"
                                    ),
                                ))
                                continue
                            # 压缩比异常（file_size == 0 时不检查）
                            if info.compress_size > 0 and info.file_size > 0:
                                ratio = info.file_size / info.compress_size
                                if ratio > 100:
                                    failed_items.append(ImportFailedItem(
                                        name=Path(info.filename).name or info.filename,
                                        reason="ZIP entry 压缩比超过 100:1，疑似 zip 炸弹，已拒绝",
                                    ))
                                    continue
                            total_uncompressed += info.file_size
                            pdf_infos.append(info)

                        # PDF 数量上限
                        if len(pdf_infos) > _ZIP_MAX_PDF_COUNT:
                            failed_items.append(ImportFailedItem(
                                name=safe_basename,
                                reason=(
                                    f"ZIP 内 PDF 数量 {len(pdf_infos)} 超过 "
                                    f"{_ZIP_MAX_PDF_COUNT} 上限，已拒绝"
                                ),
                            ))
                            continue

                        # 解压总大小上限
                        if total_uncompressed > _ZIP_MAX_TOTAL_BYTES:
                            failed_items.append(ImportFailedItem(
                                name=safe_basename,
                                reason=(
                                    f"ZIP 解压总大小超过 "
                                    f"{_ZIP_MAX_TOTAL_BYTES // 1024 // 1024} MB 上限，已拒绝"
                                ),
                            ))
                            continue

                        # 逐 entry 受控解压（不用 extractall，防路径穿越 + 同名覆盖）
                        for info in pdf_infos:
                            # P1-1/P2-c：只取 entry 基名，加 uuid 前缀保证唯一，避免同名覆盖
                            entry_base = Path(info.filename).name or f"entry_{uuid.uuid4().hex}.pdf"
                            safe_entry_name = f"{uuid.uuid4().hex}_{entry_base}"
                            out_pdf = zip_dest / safe_entry_name
                            # 双保险：确认目标路径仍在 zip_dest 内（避免极端 entry.name 绕过）
                            if not out_pdf.resolve().is_relative_to(zip_dest.resolve()):
                                continue
                            # 受控读取 entry 字节（逐 entry，不 extractall）
                            out_pdf.write_bytes(zf.read(info.filename))
                            pdf_paths.append(out_pdf)
                except zipfile.BadZipFile as exc:
                    failed_items.append(ImportFailedItem(
                        name=safe_basename,
                        reason=f"ZIP 文件损坏: {exc}",
                    ))
                    continue
                except Exception as exc:
                    failed_items.append(ImportFailedItem(
                        name=safe_basename,
                        reason=f"ZIP 解压失败: {exc}",
                    ))
                    continue

            elif fname_lower.endswith(".pdf"):
                # P1-1：单 PDF 文件也用 uuid 前缀命名，消除客户端文件名影响
                safe_pdf_name = f"{uuid.uuid4().hex}_{safe_basename}"
                pdf_out = tmp_path / safe_pdf_name
                pdf_out.write_bytes(content)
                pdf_paths.append(pdf_out)

            else:
                # 非 PDF/ZIP — 不支持，但不 crash，记录 failed
                failed_items.append(ImportFailedItem(
                    name=safe_basename,
                    reason="不支持的文件类型（仅支持 .pdf 和 .zip）",
                ))
                continue

        if not pdf_paths and not failed_items:
            raise ApiError(400, "VALIDATION_ERROR", "未找到可处理的 PDF 文件")

        # 4. 批量 ingest（幂等：命中 sha256 缓存直接复用）
        ingest_results: list[dict] = []
        if pdf_paths:
            try:
                ingest_results = await ingest_pdfs(
                    paths=pdf_paths,
                    language="en",
                    session=s,
                )
            except Exception as exc:
                log.exception("ingest_pdfs 整体失败: %s", exc)
                for p in pdf_paths:
                    failed_items.append(ImportFailedItem(
                        name=p.name,
                        reason=f"摄取失败: {exc}",
                    ))
                ingest_results = []

        # 5. 关联到项目（幂等：add_paper_to_project ON CONFLICT DO NOTHING）
        imported_count = 0
        skipped_count = 0
        paper_ids: list[int] = []

        from .repositories.project import find_project_paper

        for r in ingest_results:
            pdf_name = Path(r.get("pdf_path", "")).name or "unknown.pdf"
            status = r.get("status", "failed")

            if status == "failed" or r.get("paper_id") is None:
                failed_items.append(ImportFailedItem(
                    name=pdf_name,
                    reason=r.get("err") or "ingest 失败",
                ))
                continue

            paper_id: int = r["paper_id"]

            # 检查 paper 是否已在该项目（幂等跳过统计）
            existing_pp = await find_project_paper(s, pid, paper_id)
            if existing_pp is not None:
                skipped_count += 1
                paper_ids.append(paper_id)
                continue

            # 新关联
            try:
                pp = await project_repo.add_paper_to_project(
                    s,
                    project_id=pid,
                    paper_id=paper_id,
                    added_by="import",
                )
                # 若 default_status 不是默认 candidate，更新
                if default_status != "candidate":
                    await project_repo.set_inclusion(s, pp.id, default_status)
                imported_count += 1
                paper_ids.append(paper_id)
            except Exception as exc:
                log.exception("add_paper_to_project 失败: paper_id=%d: %s", paper_id, exc)
                failed_items.append(ImportFailedItem(
                    name=pdf_name,
                    reason=f"关联到项目失败: {exc}",
                ))

    return PapersImportResponse(
        imported=imported_count,
        skipped=skipped_count,
        failed=failed_items,
        paperIds=paper_ids,
    )


# ---- P2-T3: from-search 入库端点 ----

def _from_search_candidate_id(cand: FromSearchCandidate) -> str | None:
    return (
        cand.candidateId
        or cand.openalexId
        or cand.sciverseDocId
        or cand.sciverseUniqueId
        or cand.doi
    )


def _from_search_failure_reason(exc: Exception) -> str:
    if isinstance(exc, IntegrityError):
        detail = _short_error_message(getattr(exc, "orig", None) or exc)
        return f"数据库冲突：{detail}"
    if isinstance(exc, DataError):
        detail = _short_error_message(getattr(exc, "orig", None) or exc)
        return f"字段异常：{detail}"
    if isinstance(exc, SQLAlchemyError):
        return f"数据库异常：{_short_error_message(exc)}"
    if isinstance(exc, (TypeError, ValueError)):
        return f"字段异常：{_short_error_message(exc)}"
    return _short_error_message(exc)


@app.post(
    "/projects/{pid:int}/papers/from-search",
    response_model=FromSearchResult,
    status_code=200,
    summary="把检索候选批量入库并关联到项目（幂等）",
    description=(
        "接收来自 SearchTool 检索到的候选列表，逐条 add_paper（DOI/title dedup）→ "
        "add_paper_to_project（默认 candidate）。\n\n"
        "defaultStatus=included 时，关联后再调 set_inclusion 将状态升级为 included。\n\n"
        "幂等：同一候选（DOI/title 命中）重复提交 → skipped+1，不报错，paperIds 仍含该 id。"
    ),
    tags=["library"],
)
async def from_search_endpoint(
    pid: int,
    body: FromSearchRequest,
    s=Depends(get_session),
):
    """把检索候选批量入库并关联到项目，返回入库统计和失败明细。"""
    # 1. 校验 project 存在
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")

    imported_count = 0
    skipped_count = 0
    failed: list[FromSearchFailedItem] = []
    paper_ids: list[int] = []
    fulltext_eligible_paper_ids: list[int] = []
    fulltext_seen: set[int] = set()

    from .repositories.project import find_project_paper

    for cand in body.candidates:
        try:
            # 2. 把候选字段映射成 add_paper 所需 dict
            #    creators 存 list[str]（与 import 端点一致）
            paper_data: dict = {
                "title": cand.title,
            }
            if cand.doi:
                paper_data["doi"] = cand.doi
            if cand.authors:
                paper_data["creators"] = [
                    {"literal": a} if isinstance(a, str) else a
                    for a in cand.authors
                ]
            if cand.year is not None:
                paper_data["year"] = cand.year
            if cand.abstract:
                paper_data["abstract"] = cand.abstract
            if cand.keywords:
                paper_data["keywords"] = cand.keywords
            if cand.url:
                paper_data["url"] = cand.url
            if cand.source:
                paper_data["source"] = cand.source
            if cand.containerTitle:
                paper_data["container_title"] = cand.containerTitle
            refs = list(dict.fromkeys(str(r).strip()[:1000] for r in (cand.references or []) if str(r).strip()))
            if not refs and isinstance(cand.raw, dict):
                raw_refs = (
                    cand.raw.get("references")
                    or cand.raw.get("referencedWorks")
                    or cand.raw.get("referenced_works")
                    or []
                )
                if isinstance(raw_refs, str):
                    raw_refs = [r for r in raw_refs.split(";") if r.strip()]
                if isinstance(raw_refs, list):
                    refs = list(dict.fromkeys(str(r).strip()[:1000] for r in raw_refs if str(r).strip()))
            csl_json: dict = {}
            if refs:
                csl_json["references"] = refs[:1000]
            cited_by_count = parse_cited_by_count(cand.citedByCount)
            if cited_by_count is not None:
                csl_json["citedByCount"] = cited_by_count
            if csl_json:
                paper_data["csl_json"] = csl_json
            # openalexId 无对应 Paper 列，有意丢弃（仅用于前端去重/展示）

            # 3. 幂等写入 Paper（命中 dedup 直接返回已有行）
            paper = await lib_repo.add_paper(s, paper_data)

            external_ids: list[dict] = []
            if cand.openalexId:
                external_ids.append({
                    "provider": "openalex",
                    "id_type": "work_id",
                    "external_id": cand.openalexId,
                    "url": cand.url,
                })
            if cand.sciverseDocId:
                external_ids.append({
                    "provider": "sciverse",
                    "id_type": "doc_id",
                    "external_id": cand.sciverseDocId,
                    "url": cand.url,
                    "raw": cand.raw,
                })
            if cand.sciverseUniqueId:
                external_ids.append({
                    "provider": "sciverse",
                    "id_type": "unique_id",
                    "external_id": cand.sciverseUniqueId,
                    "url": cand.url,
                    "raw": cand.raw,
                })
            external_ids.extend(cand.externalIds or [])
            if external_ids:
                await lib_repo.upsert_external_ids(s, paper.id, external_ids)

            # 4. 检查是否已关联到该项目（幂等 skipped 统计）
            existing_pp = await find_project_paper(s, pid, paper.id)
            if existing_pp is not None:
                # B: 已存在但 defaultStatus==included → 升级 inclusion（即使已关联也纳入）
                if body.defaultStatus == "included":
                    await project_repo.set_inclusion(s, existing_pp.id, "included")
                skipped_count += 1
                paper_ids.append(paper.id)
                if cand.sciverseDocId and paper.id not in fulltext_seen:
                    fulltext_eligible_paper_ids.append(paper.id)
                    fulltext_seen.add(paper.id)
                continue

            # 5. 新关联到项目（默认 candidate）
            pp = await project_repo.add_paper_to_project(
                s,
                project_id=pid,
                paper_id=paper.id,
                added_by="search",
            )

            # 6. 若 defaultStatus==included，再调 set_inclusion 升级状态
            #    set_inclusion 按 project_paper_id（pp.id），不是 paper_id
            if body.defaultStatus == "included":
                await project_repo.set_inclusion(s, pp.id, "included")

            imported_count += 1
            paper_ids.append(paper.id)
            if cand.sciverseDocId and paper.id not in fulltext_seen:
                fulltext_eligible_paper_ids.append(paper.id)
                fulltext_seen.add(paper.id)
        except Exception as exc:
            reason = _from_search_failure_reason(exc)
            log.exception(
                "from_search: 候选处理失败 title=%r candidateId=%r: %s",
                cand.title[:60],
                _from_search_candidate_id(cand),
                reason,
            )
            # 若异常为真实 DBAPI/IntegrityError，async session 会进入 failed-transaction
            # 状态；必须 rollback 清理脏状态，确保后续候选能在干净事务上继续处理。
            try:
                await s.rollback()
            except Exception:
                pass
            failed.append(FromSearchFailedItem(
                candidateId=_from_search_candidate_id(cand),
                title=cand.title,
                reason=reason,
            ))

    return FromSearchResult(
        imported=imported_count,
        skipped=skipped_count,
        failed=failed,
        failedCount=len(failed),
        paperIds=paper_ids,
        fulltextEligiblePaperIds=fulltext_eligible_paper_ids,
    )


# ---- P3-T1: backfill-metadata 元数据补全端点 ----

@app.post(
    "/projects/{pid:int}/papers/backfill-metadata",
    response_model=BackfillMetadataResult,
    summary="AI 元数据补全：用 LLM 从已 OCR 全文回填缺失题录（P3-T1）",
    description=(
        "对项目内 OCR-done 且缺 abstract 或 creators 的文献，"
        "用 LLM 读取 Markdown 全文首部抽取元数据，仅回填当前为空的字段，不覆盖已有内容。\n\n"
        "逐篇失败隔离：单篇 LLM/JSON/DB 错误不影响批次其他篇。\n"
        "支持 X-LLM-Key 头（用户自带 key）；无 key 时回退服务端 DEEPSEEK。"
    ),
    tags=["library"],
)
async def backfill_metadata_endpoint(
    pid: int,
    body: BackfillMetadataRequest,
    request: Request,
    s=Depends(get_session),
):
    """P3-T1: LLM 从 OCR 全文批量补全缺失元数据。"""
    from .repositories.project import get_project as _get_proj
    from .models import Attachment, Paper, ProjectPaper
    from sqlalchemy import select, or_
    from sqlalchemy import func as sa_func

    # 1. 校验 project 存在
    if await _get_proj(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")

    # 2. 查询目标论文：项目内 OCR done 且（onlyMissing=True 时缺 abstract 或 creators）
    att_sq = (
        select(Attachment.paper_id)
        .where(
            Attachment.mineru_status == "done",
            Attachment.markdown_path.isnot(None),
        )
        .distinct()
        .scalar_subquery()
    )

    base_where = [
        ProjectPaper.project_id == pid,
        Paper.id.in_(att_sq),
    ]

    if body.onlyMissing:
        # 缺 abstract：为 None 或空串
        missing_abstract = or_(Paper.abstract.is_(None), Paper.abstract == "")
        # 缺 creators：为 None（JSON null）或空 JSON 数组
        missing_creators = or_(
            Paper.creators.is_(None),
            sa_func.json_array_length(Paper.creators) == 0,
        )
        base_where.append(or_(missing_abstract, missing_creators))

    # 3. 取本批次目标论文的 paper_id 列表（只取 id，避免 rollback expire 整批对象）
    #    A-fix: 只预取 Paper.id 列表，循环内 s.get 重新取新鲜对象，使 rollback 后下篇不受 expire 影响。
    id_q = (
        select(Paper.id)
        .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
        .where(*base_where)
        .limit(body.limit)
    )
    paper_ids = list((await s.execute(id_q)).scalars().all())

    if not paper_ids:
        # D-fix: 处理前也需 count 供 available 返回（此处为空批次，直接 count）
        count_q = (
            select(sa_func.count())
            .select_from(Paper)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(*base_where)
        )
        available: int = (await s.execute(count_q)).scalar_one()
        return BackfillMetadataResult(processed=0, updated=0, skipped=0, failed=0, available=available)

    # 4. 逐篇补全
    llm = _llm(request)
    processed = updated = skipped = failed = 0

    for pid_paper in paper_ids:
        # A-fix: 每篇通过 s.get 获取新鲜对象，rollback 后下篇 s.get 是干净查询，不受 expire 影响
        paper = await s.get(Paper, pid_paper)
        if paper is None:
            skipped += 1
            continue
        processed += 1
        result = await backfill_paper_metadata(s, llm, paper)
        status = result["status"]
        if status == "updated":
            updated += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1

    # D-fix: 处理完成后重新 count 真实剩余（缺元数据的篇），返回给前端"待补 N 篇"
    count_q = (
        select(sa_func.count())
        .select_from(Paper)
        .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
        .where(*base_where)
    )
    available: int = (await s.execute(count_q)).scalar_one()

    log.info(
        "backfill_metadata pid=%d: processed=%d updated=%d skipped=%d failed=%d available=%d",
        pid, processed, updated, skipped, failed, available,
    )
    return BackfillMetadataResult(
        processed=processed,
        updated=updated,
        skipped=skipped,
        failed=failed,
        available=available,
    )


# ---- P3-T3: extract-structured 结构化抽取端点 ----

@app.post(
    "/projects/{pid:int}/papers/extract-structured",
    response_model=ExtractStructuredResult,
    summary="AI 结构化抽取：用 LLM 从已 OCR 全文抽取研究要素（P3-T3）",
    description=(
        "对项目内 OCR-done 的文献，用 LLM 读取 Markdown 全文首部，"
        "抽取 research_question/method/findings/dataset/contribution 五字段，"
        "幂等 upsert 到 paper_extraction 表。\n\n"
        "reextract=false（默认）：已有 extraction 的篇自动跳过；"
        "reextract=true：强制覆盖更新。\n\n"
        "逐篇失败隔离：单篇 LLM/JSON/DB 错误不影响批次其他篇。\n"
        "支持 X-LLM-Key 头（用户自带 key）；无 key 时回退服务端 DEEPSEEK。"
    ),
    tags=["library"],
)
async def extract_structured_endpoint(
    pid: int,
    body: ExtractStructuredRequest,
    request: Request,
    s=Depends(get_session),
):
    """P3-T3: LLM 从 OCR 全文批量结构化抽取研究要素。"""
    from .repositories.project import get_project as _get_proj
    from .models import Paper, PaperExtraction, ProjectPaper
    from sqlalchemy import select, func as sa_func

    # 1. 校验 project 存在
    if await _get_proj(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")

    # 2. 构建 OCR-done 子查询，并统计无全文附件的题录文献
    att_sq = fulltext_paper_ids_subquery()
    no_fulltext = await count_no_fulltext_candidates(s, pid, reextract=body.reextract)

    # 3. 基础过滤：项目内 OCR done 的论文
    base_where = [
        ProjectPaper.project_id == pid,
        Paper.id.in_(att_sq),
    ]

    # 4. reextract=false 时，SQL 层排除已有 extraction 的篇（让 limit 作用在真正待抽取的篇上）
    if not body.reextract:
        already_extracted_sq = (
            select(PaperExtraction.paper_id)
            .scalar_subquery()
        )
        base_where.append(Paper.id.notin_(already_extracted_sq))

    # 5. 取本批次目标论文的 paper_id 列表（只取 id，避免 rollback expire 整批对象）
    #    A-fix: 只预取 Paper.id 列表，循环内 s.get 重新取新鲜对象，使 rollback 后下篇不受 expire 影响。
    id_q = (
        select(Paper.id)
        .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
        .where(*base_where)
        .limit(body.limit)
    )
    paper_ids = list((await s.execute(id_q)).scalars().all())

    if not paper_ids:
        # D-fix: 处理前也需 count 供 available 返回（此处为空批次，直接 count）
        count_q = (
            select(sa_func.count())
            .select_from(Paper)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(*base_where)
        )
        available: int = (await s.execute(count_q)).scalar_one()
        summary = (
            f"跳过 {no_fulltext} 篇：无全文附件（仅题录），可先在文献库补全文"
            if no_fulltext else None
        )
        return ExtractStructuredResult(
            processed=0,
            extracted=0,
            skipped=0,
            failed=0,
            available=available,
            noFulltext=no_fulltext,
            summary=summary,
        )

    # 6. 逐篇抽取（reextract=false 下 SQL 已排除已抽取篇，应用层只需处理无 markdown/读取失败的 skip）
    llm = _llm(request)
    processed = extracted = skipped = failed = 0
    runtime_no_fulltext = 0

    for pid_paper in paper_ids:
        # A-fix: 每篇通过 s.get 获取新鲜对象，rollback 后下篇 s.get 是干净查询，不受 expire 影响
        paper = await s.get(Paper, pid_paper)
        if paper is None:
            skipped += 1
            continue
        processed += 1

        result = await extract_paper_structured(s, llm, paper)
        status = result["status"]
        if status == "extracted":
            extracted += 1
        elif status == "skipped":
            skipped += 1
            if is_no_fulltext_skip_reason(result.get("reason")):
                runtime_no_fulltext += 1
        else:
            failed += 1

    # D-fix: 处理完成后重新 count 真实剩余（仍无 extraction 的 OCR-done 篇），返回给前端"待解析 N 篇"
    count_q = (
        select(sa_func.count())
        .select_from(Paper)
        .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
        .where(*base_where)
    )
    available: int = (await s.execute(count_q)).scalar_one()

    log.info(
        "extract_structured pid=%d: processed=%d extracted=%d skipped=%d failed=%d available=%d no_fulltext=%d",
        pid, processed, extracted, skipped, failed, available, no_fulltext + runtime_no_fulltext,
    )
    no_fulltext_total = no_fulltext + runtime_no_fulltext
    summary = (
        f"跳过 {no_fulltext_total} 篇：无全文附件（仅题录），可先在文献库补全文"
        if no_fulltext_total else None
    )
    return ExtractStructuredResult(
        processed=processed,
        extracted=extracted,
        skipped=skipped,
        failed=failed,
        available=available,
        noFulltext=no_fulltext_total,
        summary=summary,
    )


# ---- P1-6: Agent Run 端点 ----

def _get_run_controller(request: Request) -> RunController:
    return request.app.state.run_controller


def _get_publisher(request: Request) -> SubscribableEventPublisher:
    return request.app.state.publisher


@app.post("/projects/{pid}/agent/runs", response_model=AgentRunRef)
async def create_agent_run(
    pid: int,
    body: AgentRunRequest,
    request: Request,
    s=Depends(get_session),
):
    """创建并启动一个 agent run，立即返回 runId + status=running。"""
    # 修复3 (codex P1-9): 创建前校验 project 存在，避免造孤儿 run。
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")
    ctrl: RunController = _get_run_controller(request)
    run_id = await ctrl.create(
        project_id=pid,
        user_prompt=body.prompt,
        auto_confirm=body.autoConfirm,
    )
    # 修复4 (codex P1-12): 接入 X-LLM-Key → harness override（无 key 返回 None）。
    override = _llm_override(request)
    ctrl.start(run_id, llm_override=override, sciverse_override=_sciverse_run_override(request))
    return AgentRunRef(runId=run_id, projectId=pid, status="running")


@app.post(
    "/projects/{pid:int}/agent/runs/{rid:int}/confirm",
    response_model=RunControlResponse,
)
async def confirm_run(
    pid: int,
    rid: int,
    body: ConfirmRequest,
    request: Request,
    s=Depends(get_session),
):
    """对 awaiting_confirmation 的 run 放行/拒绝队首写工具，必要时续跑驱动。

    顺序错误（toolCallId 与队首不符）/ 非待确认状态 → 409；run 不存在或不属于 pid → 404。
    返回处理后状态：awaiting_confirmation（还有下一个待确认）或 running（协议完成已续跑）。
    """
    run = await agent_run_repo.get_run(s, rid)
    if run is None or run.project_id != pid:
        raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {rid} 不存在")
    ctrl: RunController = _get_run_controller(request)
    status = await ctrl.confirm(rid, body.toolCallId, body.decision)
    return RunControlResponse(status=normalize_run_status(status))


@app.post(
    "/projects/{pid:int}/agent/runs/{rid:int}/pause",
    response_model=RunControlResponse,
)
async def pause_run(pid: int, rid: int, request: Request, s=Depends(get_session)):
    """请求暂停一个运行中的 run（协作式：当前轮收尾后退出，进入 paused，流保持打开）。

    run 不存在或不属于 pid → 404。返回处理后状态（running 的 run → paused；非 running
    原样返回当前状态）。
    """
    run = await agent_run_repo.get_run(s, rid)
    if run is None or run.project_id != pid:
        raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {rid} 不存在")
    ctrl: RunController = _get_run_controller(request)
    status = await ctrl.pause(rid)
    return RunControlResponse(status=normalize_run_status(status))


@app.post(
    "/projects/{pid:int}/agent/runs/{rid:int}/resume",
    response_model=RunControlResponse,
)
async def resume_run(pid: int, rid: int, request: Request, s=Depends(get_session)):
    """恢复一个 paused 的 run：拉回 running + 后台续跑。

    run 不存在或不属于 pid → 404。返回处理后状态（paused 的 run → running；非 paused
    原样返回当前状态）。
    """
    run = await agent_run_repo.get_run(s, rid)
    if run is None or run.project_id != pid:
        raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {rid} 不存在")
    ctrl: RunController = _get_run_controller(request)
    status = await ctrl.resume(rid)
    return RunControlResponse(status=normalize_run_status(status))


@app.post(
    "/projects/{pid:int}/agent/runs/{rid:int}/cancel",
    response_model=RunControlResponse,
)
async def cancel_run(pid: int, rid: int, request: Request, s=Depends(get_session)):
    """取消一个 run（终态 cancelled）：取消活跃驱动 task + 发 cancelled 终态事件。

    run 不存在或不属于 pid → 404。返回处理后状态（cancelled；已终态的 run 幂等返回原状态）。
    """
    run = await agent_run_repo.get_run(s, rid)
    if run is None or run.project_id != pid:
        raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {rid} 不存在")
    ctrl: RunController = _get_run_controller(request)
    status = await ctrl.cancel(rid)
    return RunControlResponse(status=normalize_run_status(status))


@app.get("/projects/{pid}/agent/runs/{rid}/events")
async def agent_run_events(
    pid: int,
    rid: int,
    request: Request,
    s=Depends(get_session),
):
    """SSE 流：先订阅实时事件，再补发历史（DB 权威），最后转发实时队列，按 seq 去重。

    修复1 (codex P1-3) — 竞态：必须**先 subscribe 拿到 queue，再查历史**。否则
      list_events 与 subscribe 之间产生的事件（尤其终态 run_complete）会丢，客户端
      永远卡 heartbeat。先订阅 → 队列开始缓冲 → 再读历史发送 → 进入实时循环，对
      队列里 seq <= 已发历史最大 seq 的事件跳过（去重），避免历史与实时双发。
    修复2 (codex P1-4) — 历史事件缺 seq：历史与实时统一让 SSE data 含 seq、每帧带
      id 行（见下方 SSE seq 契约）。
    修复3 (codex P1-9) — run 校验：SSE 开始前校验 run 存在且 run.project_id == pid，
      不存在直接 404（不进 SSE 无限 heartbeat）。
    """
    publisher: SubscribableEventPublisher = _get_publisher(request)
    channel = f"run:{rid}:events"

    # 修复3: SSE 开始前校验 run 归属，避免进入无限 heartbeat 流。
    run = await agent_run_repo.get_run(s, rid)
    if run is None or run.project_id != pid:
        raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {rid} 不存在")

    # 解析 Last-Event-ID（断点续传）
    raw_lei = request.headers.get("last-event-id", "0")
    try:
        last_id = int(raw_lei)
    except (ValueError, TypeError):
        last_id = 0

    from .harness.events import EventType

    # P3-1: cancelled 也是终态 → SSE 收到即关流，不再空等 heartbeat。
    # paused/resumed 是非终态信息事件（流保持打开，等 resume），不入此集合。
    terminal_types = {EventType.RUN_COMPLETE, EventType.ERROR, EventType.CANCELLED}

    def _run_event_payload(ev_type: str, payload: dict) -> dict:
        """历史事件读路径归一：deprecated completed 只在入口兼容，不再对外输出。"""
        data = dict(payload or {})
        if "status" in data and ev_type in {
            EventType.RUN_COMPLETE, EventType.PAUSED, EventType.RESUMED, EventType.CANCELLED,
        }:
            data["status"] = normalize_run_status(data.get("status"))
        return data

    # 修复1: 先订阅，再查历史。subscribe 后队列即开始缓冲，覆盖「查历史—进实时」
    # 中间窗口产生的事件，再用 seq 去重避免与历史重复。
    q = publisher.subscribe(channel)
    # 历史事件在进入 StreamingResponse 前取好（get_session 依赖在此处有效）
    history = await agent_run_repo.list_events(s, rid, after_seq=last_id)

    async def gen():
        try:
            # 1) 先补发历史事件（来自 DB，权威来源）；记录已发最大 seq 供实时去重。
            #    修复2: data 含 seq（{**payload, "seq": seq}），SSE 帧 id 用 ev.seq。
            max_seq_sent = last_id
            history_terminal = False
            for ev in history:
                yield _sse(ev.type, {**_run_event_payload(ev.type, ev.payload or {}), "seq": ev.seq}, seq=ev.seq)
                if ev.seq > max_seq_sent:
                    max_seq_sent = ev.seq
                if ev.type in terminal_types:
                    history_terminal = True

            # 2) 历史已含终态事件 → run 已结束，无需进实时循环。
            if history_terminal:
                return

            # 3) 转发实时队列；对 seq <= 已发历史最大 seq 的事件跳过（去重，修复1）。
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": hb\n\n"
                    continue
                ev_seq = ev.get("seq")
                # 去重：实时事件 seq 若 <= 历史已发最大 seq，说明已在历史里发过，跳过。
                if ev_seq is not None and ev_seq <= max_seq_sent:
                    continue
                ev_type = ev.get("type", "")
                yield _sse(ev_type, _run_event_payload(ev_type, ev), seq=ev_seq)
                if ev_seq is not None and ev_seq > max_seq_sent:
                    max_seq_sent = ev_seq
                if ev_type in terminal_types:
                    break
        finally:
            publisher.unsubscribe(channel, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/projects/{pid}/agent/runs")
async def list_agent_runs(pid: int, s=Depends(get_session)):
    """列出某 project 的所有 agent runs。"""
    runs = await agent_run_repo.list_runs(s, project_id=pid)
    return {
        "runs": [
            AgentRunRef(runId=r.id, projectId=r.project_id, status=normalize_run_status(r.status))
            for r in runs
        ]
    }


@app.get("/projects/{pid}/agent/runs/{rid}", response_model=RunDetail)
async def get_agent_run(pid: int, rid: int, s=Depends(get_session)):
    """查询单个 agent run 的详情（status / roundsLog / finalOutput / evidenceRefs）。"""
    run = await agent_run_repo.get_run(s, rid)
    if run is None or run.project_id != pid:
        raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {rid} 不存在")
    return RunDetail(
        runId=run.id,
        status=normalize_run_status(run.status),
        roundsLog=run.rounds_log or [],
        finalOutput=run.final_output,
        evidenceRefs=run.evidence_refs,
    )


@app.get("/projects/{pid:int}/agent/runs/{rid:int}/runlog")
async def get_runlog(pid: int, rid: int, s=Depends(get_session)):
    """导出可验证运行日志（RunLog, schema=runlog/v1）——MinerU Track 2 头号交付物。"""
    run = await agent_run_repo.get_run(s, rid)
    if run is None or run.project_id != pid:
        raise ApiError(404, "RUN_NOT_FOUND", "run 不存在")
    return JSONResponse(
        content=await build_runlog(s, rid),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="runlog_{rid}.json"'},
    )


@app.get("/projects/{pid:int}/agent/runs/{rid:int}/grounding")
async def get_grounding(pid: int, rid: int, s=Depends(get_session)):
    """grounding 可信凭证摘要（camelCase）——TrustCard 数据源。

    从 RunLog（manifest + evidence_refs）+ 该项目 included 语料的源文档哈希集合，
    计算 grounding 三率与哈希链/事件计数。空引用时三率为 None（不可评分，诚实标注，
    不伪装满分）。
    """
    run = await agent_run_repo.get_run(s, rid)
    if run is None or run.project_id != pid:
        raise ApiError(404, "RUN_NOT_FOUND", "run 不存在")

    runlog = await build_runlog(s, rid)
    corpus_hashes = await project_corpus_content_hashes(s, pid)
    metrics = grounding_metrics(runlog, corpus_hashes)

    manifest = runlog["manifest"]
    return {
        "runId": rid,
        "status": runlog["run"]["status"],
        "modelUsed": runlog["run"]["model_used"],
        "createdAt": runlog["run"]["created_at"],
        "manifest": {
            "eventCount": manifest["event_count"],
            "toolInvocationCount": manifest["tool_invocation_count"],
            "evidenceCount": manifest["evidence_count"],
            "fabricatedCount": manifest["fabricated_count"],
            "chainHead": manifest["chain_head"],
            "contentSha256": manifest["content_sha256"],
        },
        "metrics": {
            "groundingAccuracy": metrics["grounding_accuracy"],
            "provenanceHitRate": metrics["provenance_hit_rate"],
            "zeroFabricationRate": metrics["zero_fabrication_rate"],
            "insufficientEvidence": metrics["insufficient_evidence"],
            "scoreable": metrics["scoreable"],
            "evidenceCount": metrics["evidence_count"],
            "fabricatedCount": metrics["fabricated_count"],
            "greenCount": metrics["green_count"],
            "yellowCount": metrics["yellow_count"],
        },
        "corpusHashCount": len(corpus_hashes),
        "verifyHint": "python scripts/verify_runlog.py <runlog.json> --corpus-hashes <hashes.json>",
    }


# ---- M4: 工件 (Artifact) CRUD 端点 ----

from sqlalchemy import select as _select
from .models import Artifact as _Artifact


def _artifact_to_item(a: _Artifact) -> ArtifactItem:
    """ORM 行 → Pydantic 响应体。"""
    return ArtifactItem(
        id=a.id,
        projectId=a.project_id,
        runId=a.run_id,
        type=a.type,
        title=a.title,
        sourceEventSeq=a.source_event_seq,
        contentRef=a.content_ref,
        pinned=a.pinned,
        userAnnotation=a.user_annotation,
        order=a.order,
        createdAt=a.created_at.isoformat() if a.created_at else None,
    )


@app.get(
    "/projects/{pid:int}/artifacts",
    response_model=dict,
    summary="列出项目工件（M4）",
    tags=["artifact"],
)
async def list_artifacts(
    pid: int,
    pinned: Optional[bool] = None,
    s=Depends(get_session),
):
    """GET /projects/{pid}/artifacts — 列出项目工件，可按 pinned 过滤。"""
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")
    q = _select(_Artifact).where(_Artifact.project_id == pid)
    if pinned is not None:
        q = q.where(_Artifact.pinned == pinned)
    q = q.order_by(_Artifact.order.asc(), _Artifact.created_at.desc())
    rows = (await s.execute(q)).scalars().all()
    return {"artifacts": [_artifact_to_item(a) for a in rows]}


@app.post(
    "/projects/{pid:int}/artifacts",
    response_model=ArtifactItem,
    status_code=201,
    summary="创建/pin 工件（M4）",
    tags=["artifact"],
)
async def create_artifact(
    pid: int,
    body: ArtifactCreateRequest,
    s=Depends(get_session),
):
    """POST /projects/{pid}/artifacts — 创建工件（内容由前端从 RunLog 派生，此处持久化身份）。"""
    if await project_repo.get_project(s, pid) is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", f"项目 {pid} 不存在")
    # 若指定了 run_id，校验归属
    if body.runId is not None:
        run = await agent_run_repo.get_run(s, body.runId)
        if run is None or run.project_id != pid:
            raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {body.runId} 不存在或不属于项目 {pid}")
    artifact = _Artifact(
        project_id=pid,
        run_id=body.runId,
        type=body.type,
        title=body.title,
        source_event_seq=body.sourceEventSeq,
        content_ref=body.contentRef,
        pinned=body.pinned,
        user_annotation=body.userAnnotation,
        order=body.order,
    )
    s.add(artifact)
    await s.commit()
    await s.refresh(artifact)
    return _artifact_to_item(artifact)


@app.patch(
    "/projects/{pid:int}/artifacts/{aid:int}",
    response_model=ArtifactItem,
    summary="更新工件 title/annotation/pinned/order（M4）",
    tags=["artifact"],
)
async def patch_artifact(
    pid: int,
    aid: int,
    body: ArtifactPatchRequest,
    s=Depends(get_session),
):
    """PATCH /projects/{pid}/artifacts/{aid} — 改 title/annotation/pinned/order。"""
    row = (
        await s.execute(_select(_Artifact).where(_Artifact.id == aid, _Artifact.project_id == pid))
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "ARTIFACT_NOT_FOUND", f"工件 {aid} 不存在")
    # codex M4-P2#4: 用 model_fields_set 区分"未传字段"与"显式传 null",
    # 使 userAnnotation=null 能清空已有标注(而非被 is-not-None 跳过)。
    fields_set = body.model_fields_set
    if body.title is not None:
        row.title = body.title
    if body.pinned is not None:
        row.pinned = body.pinned
    if "userAnnotation" in fields_set:
        row.user_annotation = body.userAnnotation
    if body.order is not None:
        row.order = body.order
    await s.commit()
    await s.refresh(row)
    return _artifact_to_item(row)


@app.delete(
    "/projects/{pid:int}/artifacts/{aid:int}",
    status_code=204,
    summary="删除工件（M4）",
    tags=["artifact"],
)
async def delete_artifact(
    pid: int,
    aid: int,
    s=Depends(get_session),
):
    """DELETE /projects/{pid}/artifacts/{aid} — 删除工件身份记录（不影响 RunLog 内容）。"""
    row = (
        await s.execute(_select(_Artifact).where(_Artifact.id == aid, _Artifact.project_id == pid))
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "ARTIFACT_NOT_FOUND", f"工件 {aid} 不存在")
    await s.delete(row)
    await s.commit()
    return Response(status_code=204)
