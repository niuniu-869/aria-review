"""响应模型, 镜像 packages/contracts/openapi.yaml。

注: 这是手写镜像; 设计 §12 的目标是 schema-first 由契约生成 (Codex-10)。
Phase 0 先手写 + 测试快照守护, 后续接 openapi-typescript / datamodel-code-generator。
"""
from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .run_status import RunStatus
from .search_limits import SEARCH_LIMIT_MAX

# 通用 POST /ai/jobs 可创建的 kind（_run_ai_job 有执行分支的集合）
AiJobCreatableKind = Literal[
    "review",
    "chat",
    "summary",
    "translate",
    "rewrite",
    "infographic_prompt",
    "infographic_image",
]
# 已持久化/可列出的 kind 全集：research 路由（gaps:discover / gaps:verify / gaps:feasibility）
# 走专用端点创建 gap_discover / gap_verify / gap_feasibility，不经通用创建入口
AiJobKind = Literal[
    "review",
    "chat",
    "summary",
    "translate",
    "rewrite",
    "infographic_prompt",
    "infographic_image",
    "gap_discover",
    "gap_verify",
    "gap_feasibility",
]
AiJobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
CorpusStatus = Literal["parsing", "ready", "failed"]
InclusionStatus = Literal["candidate", "included", "excluded", "maybe"]
OcrStatus = Literal["none", "pending", "processing", "done", "failed"]


class Health(BaseModel):
    status: str
    service: str
    rService: str  # up | down | unknown


class PublicStats(BaseModel):
    """公开着陆页统计（免认证）：真实入库规模，用于 welcome 展示。"""
    papers: int = Field(ge=0)          # 入库文献总数
    blockAnchors: int = Field(ge=0)    # 块级溯源锚点数（全文精读文档的块总数）
    dois: int = Field(ge=0)            # 去重 DOI 数


class AnnualPoint(BaseModel):
    year: int | None = None
    articles: int | None = Field(default=None, ge=0)


class OverviewStats(BaseModel):
    documents: int | None = Field(default=None, ge=0)
    sources: int | None = Field(default=None, ge=0)
    authors: int | None = Field(default=None, ge=0)
    keywordsPlus: int | None = Field(default=None, ge=0)
    authorKeywords: int | None = Field(default=None, ge=0)
    avgCitationsPerDoc: float | None = Field(default=None, ge=0)
    timespanFrom: int | None = None
    timespanTo: int | None = None
    # A4 可选增量: 语料级 H 指数 / 年均增长率(CAGR, %), 缺/边界→None
    hIndex: int | None = Field(default=None, ge=0)
    annualGrowthRate: float | None = None


class OverviewResult(BaseModel):
    schemaVersion: int
    projectId: str
    corpusId: str
    stats: OverviewStats
    annualProduction: list[AnnualPoint]


class ReviewRequest(BaseModel):
    type: str  # 论型: undergrad/master/phd/grant/proposal/sci_intro
    topic: str = Field(min_length=1, max_length=500)


class AiJobCreateRequest(BaseModel):
    kind: AiJobCreatableKind
    corpusId: str | None = None
    type: str | None = None
    topic: str | None = None
    query: str | None = None
    history: list[dict] = Field(default_factory=list)
    text: str | None = None
    direction: str | None = None
    action: str | None = None
    style: str | None = None
    imagePrompt: str | None = None


class AnalyticsEventRequest(BaseModel):
    """产品埋点上报（0.6.1 P0）。event 为漏斗事件名，props 为轻量上下文。"""
    model_config = ConfigDict(extra="forbid")
    event: str = Field(min_length=1, max_length=48)
    projectId: int | None = None
    props: dict | None = None


class ImageSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseUrl: str | None = None
    model: str | None = None
    size: str | None = None


class ImagePingResult(BaseModel):
    ok: bool
    model: str
    baseUrl: str
    size: str
    detail: str | None = None


class AiJobItem(BaseModel):
    id: int
    projectId: int
    corpusId: str | None = None
    kind: AiJobKind
    status: AiJobStatus
    request: dict | None = None
    resultText: str = ""
    annotatedText: str | None = None
    summary: dict | None = None
    provenanceMap: dict | None = None
    events: list[dict] = Field(default_factory=list)
    error: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    completedAt: str | None = None


# --- 切片3 分析页 ---
class SourceItem(BaseModel):
    source: str | None = None
    articles: int | None = Field(default=None, ge=0)


class HSourceItem(BaseModel):
    source: str | None = None
    h: int | None = Field(default=None, ge=0)
    # A4 可选增量: g 指数 / m 指数(缺基准年→None) / 被引总数 tc
    g: int | None = None
    m: float | None = None
    tc: int | None = None


class BradfordItem(BaseModel):
    source: str | None = None
    zone: str | None = None
    freq: int | None = Field(default=None, ge=0)
    # A4 可选增量: 排名(行号) + 累计频次百分比(0-100)
    rank: int | None = None
    cumPct: float | None = None


class SourcesResult(BaseModel):
    schemaVersion: int
    projectId: str
    corpusId: str
    topSources: list[SourceItem]
    hIndex: list[HSourceItem]
    bradford: list[BradfordItem]


class AuthorItem(BaseModel):
    author: str | None = None
    articles: int | None = Field(default=None, ge=0)


class HAuthorItem(BaseModel):
    author: str | None = None
    h: int | None = Field(default=None, ge=0)
    # A4 可选增量: g 指数 / m 指数(缺基准年→None) / 被引总数 tc
    g: int | None = None
    m: float | None = None
    tc: int | None = None


class LotkaPoint(BaseModel):
    articles: int | None = Field(default=None, ge=0)
    authors: int | None = Field(default=None, ge=0)


class Lotka(BaseModel):
    beta: float | None = None
    distribution: list[LotkaPoint] = Field(default_factory=list)


class AuthorsResult(BaseModel):
    schemaVersion: int
    projectId: str
    corpusId: str
    topAuthors: list[AuthorItem]
    hIndex: list[HAuthorItem]
    lotka: Lotka


class CitedDoc(BaseModel):
    title: str | None = None
    author: str | None = None
    year: int | None = None
    cited: int | None = Field(default=None, ge=0)


class KeywordItem(BaseModel):
    term: str | None = None
    freq: int | None = Field(default=None, ge=0)


class DocumentsResult(BaseModel):
    schemaVersion: int
    projectId: str
    corpusId: str
    topCited: list[CitedDoc]
    keywords: list[KeywordItem]


# --- A4 统一可用性契约 (AnalysisEnvelope, spec §4.0) ---
# 判别式联合按 available 字段: Annotated[Union[XxxOk, AnalysisUnavailable],
#   Field(discriminator="available")]。available:true→Ok(必含 data)、false→Unavailable
#   (必含 message), 判别器据此严格分支, 混合信封 (true+reason / false+data) 被拒。
# 各信封模型 extra="forbid": 顶层键由 _proxy_envelope 完全控制 (available/data 或
#   reason 等 + schemaVersion/corpusId/projectId 全部声明), 杜绝意外字段静默通过。

AnalysisUnavailableReason = Literal[
    "missing_field", "not_enough_data", "computed_empty", "analysis_error"
]


class AnalysisUnavailable(BaseModel):
    """available:false 半信封。所有高级图共用此降级形状。"""
    model_config = ConfigDict(extra="forbid")
    available: Literal[False]
    reason: AnalysisUnavailableReason
    missingField: str | None = None
    message: str
    howto: str | None = None
    detail: str | None = None
    # 信封端点透传锚点
    schemaVersion: int | None = None
    corpusId: str | None = None
    projectId: str | None = None


# 作者年度产出 (热力图: 作者 × 年份)
class AuthorProductionCell(BaseModel):
    author: str | None = None
    year: int | None = None
    articles: int | None = Field(default=None, ge=0)


class AuthorProductionData(BaseModel):
    authors: list[str | None]
    years: list[int | None]
    cells: list[AuthorProductionCell]


class AuthorProductionOk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: Literal[True]
    data: AuthorProductionData
    schemaVersion: int | None = None
    corpusId: str | None = None
    projectId: str | None = None


AuthorProductionEnvelope = Annotated[
    Union[AuthorProductionOk, AnalysisUnavailable], Field(discriminator="available")
]


# 关键词历时演变 (themeRiver / 堆叠面积)
class KeywordTrendCell(BaseModel):
    year: int | None = None
    term: str | None = None
    freq: int | None = Field(default=None, ge=0)


class KeywordTrendData(BaseModel):
    years: list[int | None]
    terms: list[str | None]
    cells: list[KeywordTrendCell]


class KeywordTrendOk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: Literal[True]
    data: KeywordTrendData
    schemaVersion: int | None = None
    corpusId: str | None = None
    projectId: str | None = None


KeywordTrendEnvelope = Annotated[
    Union[KeywordTrendOk, AnalysisUnavailable], Field(discriminator="available")
]


# 高被引参考文献 (参考文献 | 次数)
class CitedRefItem(BaseModel):
    ref: str | None = None
    count: int | None = Field(default=None, ge=0)


class CitedRefsOk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: Literal[True]
    data: list[CitedRefItem]
    schemaVersion: int | None = None
    corpusId: str | None = None
    projectId: str | None = None


CitedRefsEnvelope = Annotated[
    Union[CitedRefsOk, AnalysisUnavailable], Field(discriminator="available")
]


# --- A5 高级图② 信封 (照 A4 模式: extra=forbid + available 判别器) ---

# 主题战略图 (Callon 中心度×密度 四象限散点)
class ThematicCluster(BaseModel):
    label: str | None = None
    centrality: float | None = None
    density: float | None = None
    freq: int | None = Field(default=None, ge=0)


class ThematicData(BaseModel):
    clusters: list[ThematicCluster]


class ThematicOk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: Literal[True]
    data: ThematicData
    schemaVersion: int | None = None
    corpusId: str | None = None
    projectId: str | None = None


ThematicEnvelope = Annotated[
    Union[ThematicOk, AnalysisUnavailable], Field(discriminator="available")
]


# 主题演进图 (多周期主题流 / Sankey)
class EvolutionNode(BaseModel):
    name: str | None = None
    period: str | None = None
    id: int | None = None


class EvolutionLink(BaseModel):
    source: int | None = None
    target: int | None = None
    value: float | None = None


class EvolutionData(BaseModel):
    nodes: list[EvolutionNode]
    links: list[EvolutionLink]


class EvolutionOk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: Literal[True]
    data: EvolutionData
    schemaVersion: int | None = None
    corpusId: str | None = None
    projectId: str | None = None


EvolutionEnvelope = Annotated[
    Union[EvolutionOk, AnalysisUnavailable], Field(discriminator="available")
]


# 历史引文图 (时序分层引用脉络)
class HistciteNode(BaseModel):
    id: str | None = None
    # 年份缺失 → None (前端布局兜底)
    year: int | None = None
    label: str | None = None
    localCites: int | None = Field(default=None, ge=0)


class HistciteEdge(BaseModel):
    # 引用方 → 被引方 (节点 id)
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    model_config = ConfigDict(populate_by_name=True)


class HistciteData(BaseModel):
    nodes: list[HistciteNode]
    edges: list[HistciteEdge]


class HistciteOk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: Literal[True]
    data: HistciteData
    schemaVersion: int | None = None
    corpusId: str | None = None
    projectId: str | None = None


HistciteEnvelope = Annotated[
    Union[HistciteOk, AnalysisUnavailable], Field(discriminator="available")
]


# 三字段 Sankey (作者 → 关键词 → 来源)
class ThreeFieldNode(BaseModel):
    name: str | None = None
    layer: int | None = Field(default=None, ge=0, le=2)  # 0=作者/1=关键词/2=来源


class ThreeFieldLink(BaseModel):
    source: str | None = None
    target: str | None = None
    value: int | None = Field(default=None, ge=0)


class ThreeFieldData(BaseModel):
    nodes: list[ThreeFieldNode]
    links: list[ThreeFieldLink]


class ThreeFieldOk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: Literal[True]
    data: ThreeFieldData
    schemaVersion: int | None = None
    corpusId: str | None = None
    projectId: str | None = None


ThreeFieldEnvelope = Annotated[
    Union[ThreeFieldOk, AnalysisUnavailable], Field(discriminator="available")
]


# --- 切片4 网络页 ---
class GraphNode(BaseModel):
    id: str | None = None
    label: str | None = None
    value: float | None = Field(default=None, ge=0)


class GraphEdge(BaseModel):
    source: str | None = None
    target: str | None = None
    weight: float | None = Field(default=None, ge=0)


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class NetworkResult(BaseModel):
    schemaVersion: int
    projectId: str
    corpusId: str
    network: str | None = None
    graph: Graph


class SocialResult(BaseModel):
    schemaVersion: int
    projectId: str
    corpusId: str
    authorCollab: Graph
    countryCollab: Graph


# --- 切片5 PRISMA ---
class PrismaRequest(BaseModel):
    # strict: 拒绝 "1"/true/1.0 强转 (Codex slice5-P2)
    identified: int = Field(ge=0, strict=True)
    duplicates: int = Field(ge=0, strict=True)
    screened: int = Field(ge=0, strict=True)
    excluded: int = Field(ge=0, strict=True)
    included: int = Field(ge=0, strict=True)


class PrismaStage(BaseModel):
    key: str
    label: str
    count: int


class PrismaResult(BaseModel):
    schemaVersion: int
    stages: list[PrismaStage]
    warnings: list[str] = Field(default_factory=list)


# --- 切片6 AI 功能 ---
class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    direction: str  # en2zh | zh2en


class RewriteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    action: str  # counter | compress | expand | casual


class SummaryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class TextResult(BaseModel):
    text: str


class ScreenRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


class ScreenItem(BaseModel):
    idx: int
    relevance: int | None = None
    reason: str


class ScreenResult(BaseModel):
    results: list[ScreenItem]


class ChatMessage(BaseModel):
    role: str  # user | assistant
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # 上限防请求体打爆解析/内存/token (Codex slice6-P1); 客户端历史视为不可信
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class CiteResult(BaseModel):
    schemaVersion: int
    projectId: str
    corpusId: str
    style: str
    citations: list[str]


# A7: 报告导出选项 (镜像 openapi ReportOptions)。
# sections 为枚举子集; prismaCounts/reviewMarkdown 为可选外部内容 (前端有则传)。
ReportSection = Literal[
    "overview", "sources", "authors", "documents", "references", "prisma", "review"
]


class PrismaCounts(BaseModel):
    identified: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    screened: int = Field(ge=0)
    excluded: int = Field(ge=0)
    included: int = Field(ge=0)


class ReportOptions(BaseModel):
    title: str = Field(default="文献计量分析报告", min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    # min_length=1: 省略→默认全章节; 但显式空数组应被拒(422), 否则与 main.py 的
    # "references in sections" citations 取数判定错配 (codex A7 P2)。
    sections: list[ReportSection] = Field(
        default_factory=lambda: ["overview", "sources", "authors", "documents", "references"],
        min_length=1,
    )
    # 可选外部内容 (YAGNI: 仅当前端提供时对应 section 才有内容)。
    prismaCounts: PrismaCounts | None = None
    reviewMarkdown: str | None = Field(default=None, max_length=200000)


class TopicRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    n: int = Field(default=50, ge=1, le=200)
    since: str = Field(default="2016-01-01")  # YYYY-MM-DD, 端点会校验格式
    withRefs: bool = True


class RefsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    withRefs: bool = True


class CorpusRef(BaseModel):
    corpusId: str
    projectId: str
    status: CorpusStatus
    schemaVersion: int
    dbsource: str | None = None
    documentCount: int | None = None
    error: str | None = None
    createdAt: str | None = None


# --- P1-7 领域 REST API ---

class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    researchQuestion: str | None = None
    description: str | None = None


class ProjectRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("项目名不能为空")
        return v


class ProjectRef(BaseModel):
    id: int
    name: str
    createdAt: str | None = None


class ActiveCorpusDetail(BaseModel):
    """项目当前 active corpus 的摘要，内嵌于 ProjectDetail。

    corpusId   — Postgres DB corpus.id（整数，物化/stale 重算时使用）。
    rCorpusId  — R 服务返回的字符串 ID（调分析端点时透传）；status != ready 时为 null。
    status     — parsing | ready | failed。
    documentCount — 当前 corpus 包含的文献篇数。
    contentHash   — 本 corpus 快照时的 included 集合指纹。
    stale      — True = 当前 included 集合与本 corpus 的 contentHash 不同，需重算。
    """
    corpusId: int
    rCorpusId: str | None = None
    status: CorpusStatus
    documentCount: int
    contentHash: str
    stale: bool
    errorReason: str | None = None


class LatestCorpusDetail(BaseModel):
    """项目最近一次 corpus 构建摘要，不按 status 过滤。"""
    corpusId: int
    rCorpusId: str | None = None
    status: CorpusStatus
    documentCount: int | None = None
    contentHash: str
    errorReason: str | None = None
    createdAt: str | None = None


class ProjectDetail(BaseModel):
    id: int
    name: str
    researchQuestion: str | None = None
    description: str | None = None
    paperCount: int
    includedCount: int
    readableFulltextCount: int
    # M2: 项目当前 active corpus（最新 ready corpus；无则 null）
    activeCorpus: ActiveCorpusDetail | None = None
    # P1-2: 项目最近一次构建（含 failed），用于前端展示失败原因
    latestCorpus: LatestCorpusDetail | None = None


class ProjectPaperItem(BaseModel):
    paperId: int
    title: str | None = None
    containerTitle: str | None = None
    year: int | None = None
    inclusionStatus: InclusionStatus
    screeningScore: int | None = None
    hasAbstract: bool = False
    hasPdf: bool = False
    ocrStatus: OcrStatus = "none"
    hasExtraction: bool = False


class PaperExtractionDto(BaseModel):
    """单篇文献的结构化抽取结果（W5-b）。"""
    researchQuestion: str | None = None
    method: str | None = None
    findings: str | None = None
    dataset: str | None = None
    contribution: str | None = None


class PaperDetail(BaseModel):
    paperId: int
    title: str | None = None
    containerTitle: str | None = None
    creators: list = Field(default_factory=list)
    doi: str | None = None
    abstract: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: list = Field(default_factory=list)
    inclusionStatus: InclusionStatus
    extraction: PaperExtractionDto | None = None
    sciverseDocId: str | None = Field(
        default=None, description="Sciverse 全文 doc_id（存在即可拉取全文）")
    hasReadableFulltext: bool = Field(
        default=False, description="是否已有可读 markdown 全文附件（GAP 精读前置条件）")


class InclusionPatchRequest(BaseModel):
    inclusionStatus: InclusionStatus
    exclusionReason: str | None = None
    screeningScore: int | None = None


# --- M1 导入端点 ---

class ImportFailedItem(BaseModel):
    """单篇导入失败详情。"""
    name: str
    reason: str


class PapersImportResponse(BaseModel):
    """POST /projects/{pid}/papers/import 响应体。"""
    imported: int = Field(ge=0, description="新导入（首次）的文献数")
    skipped: int = Field(ge=0, description="幂等跳过（已在项目中）的文献数")
    failed: list[ImportFailedItem] = Field(
        default_factory=list,
        description="解析/入库失败的文献清单",
    )
    paperIds: list[int] = Field(
        default_factory=list,
        description="所有成功入库的 paper DB id（含 imported + skipped）",
    )


# --- P1-6 Agent Run 端点 ---

class AgentRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)
    autoConfirm: bool = Field(
        default=False,
        description=(
            "True：写工具直接执行（仍走幂等短路）。False：写工具触发人工确认，"
            "run 进入 awaiting_confirmation 并发 tool_confirm_required，需经 confirm 端点放行。"
        ),
    )
    entry: str | None = Field(
        default=None,
        description=(
            "P0 三入口隔离：search（检索建库）/ review（综述撰写）/ gap（研究空白对话）。"
            "省略或 null → legacy 全工具入口（无收窄，向后兼容旧 workbench）；后端对枚举外的"
            "未知字符串也防御性归一为 legacy，但契约只保证省略/null 的 legacy 语义。"
            "据此收窄 tool_ids + 选 system persona，并只回放同 entry 的对话历史。"
        ),
    )


class ConfirmRequest(BaseModel):
    toolCallId: str
    decision: Literal["approve", "reject"]


class RunControlResponse(BaseModel):
    status: RunStatus


class AgentRunRef(BaseModel):
    runId: int
    projectId: int
    status: RunStatus


class RunDetail(BaseModel):
    runId: int
    status: RunStatus
    prompt: str | None = None  # 本 run 的原始用户指令（取自 messages_snapshot 顶层 user_prompt）
    roundsLog: list = Field(default_factory=list)
    finalOutput: str | None = None
    evidenceRefs: list | None = None


# --- M2: Corpus 物化端点 ---

class CorpusMaterializeResponse(BaseModel):
    """POST /projects/{pid}/corpus/materialize 响应体。

    corpusId    — Postgres DB corpus.id（整数）。
    rCorpusId   — R 字符串 ID；status=parsing/failed 时为 null。
    status      — parsing（R 调用进行中）| ready（已完成）| failed（出错）。
    documentCount — 本次 corpus 包含的文献篇数。
    contentHash   — 本次 included 集合指纹（幂等键）。
    """
    corpusId: int
    rCorpusId: str | None = None
    status: CorpusStatus
    documentCount: int
    contentHash: str


# --- M4 工件 ---

class ArtifactItem(BaseModel):
    """单个工件的响应体（GET list / POST create / PATCH update）。"""
    id: int
    projectId: int
    runId: int | None = None
    type: str  # review|analysis|extraction|paperset
    title: str
    sourceEventSeq: int | None = None
    contentRef: str | None = None
    pinned: bool
    userAnnotation: str | None = None
    order: int
    createdAt: str | None = None


class ArtifactCreateRequest(BaseModel):
    """POST /projects/{pid}/artifacts 请求体。"""
    type: str = Field(default="review", description="review|analysis|extraction|paperset")
    title: str = Field(default="", max_length=500)
    runId: int | None = None
    sourceEventSeq: int | None = None
    contentRef: str | None = None
    pinned: bool = False
    userAnnotation: str | None = None
    order: int = 0


class ArtifactPatchRequest(BaseModel):
    """PATCH /projects/{pid}/artifacts/{aid} 请求体（部分更新）。"""
    title: str | None = None
    pinned: bool | None = None
    userAnnotation: str | None = None
    order: int | None = None


# --- W1 文献库统计 ---

class OcrBreakdown(BaseModel):
    done: int
    processing: int
    pending: int
    failed: int
    none: int


class LibraryStats(BaseModel):
    totalPapers: int
    withMetadata: int
    withPdf: int
    ocr: OcrBreakdown


class InclusionBreakdown(BaseModel):
    included: int
    candidate: int
    excluded: int
    maybe: int


class ProjectLibraryStats(BaseModel):
    projectPapers: int
    inclusion: InclusionBreakdown
    withMetadata: int
    withPdf: int
    ocr: OcrBreakdown


# --- P2-T3 from-search 入库端点 ---

class FromSearchCandidate(BaseModel):
    """单条检索候选，title 必填，其余可选。"""
    candidateId: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=1000)
    doi: str | None = Field(default=None, max_length=255)
    authors: list[str] = Field(default_factory=list, max_length=100)
    year: int | None = Field(default=None, ge=1500, le=2100)
    abstract: str | None = Field(default=None, max_length=20000)
    keywords: str | None = Field(default=None, max_length=4000)
    containerTitle: str | None = Field(default=None, max_length=1000)
    url: str | None = Field(default=None, max_length=2000)
    openalexId: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=40)
    provider: str | None = Field(default=None, max_length=40)
    sciverseDocId: str | None = Field(default=None, max_length=255)
    sciverseUniqueId: str | None = Field(default=None, max_length=255)
    citedByCount: int | float | str | None = None
    references: list[str] = Field(default_factory=list, max_length=1000)
    externalIds: list[dict] = Field(default_factory=list, max_length=20)
    raw: dict | None = None


class FromSearchRequest(BaseModel):
    """POST /projects/{pid}/papers/from-search 请求体。"""
    candidates: list[FromSearchCandidate] = Field(min_length=1, max_length=SEARCH_LIMIT_MAX)
    defaultStatus: Literal["candidate", "included"] = "candidate"


class FromSearchFailedItem(BaseModel):
    """单条 from-search 入库失败明细。"""
    candidateId: str | None = None
    title: str
    reason: str


class FromSearchResult(BaseModel):
    """POST /projects/{pid}/papers/from-search 响应体。"""
    imported: int = Field(ge=0, description="本次新导入（首次关联到项目）的文献数")
    skipped: int = Field(ge=0, description="幂等跳过（paper 已存在于项目）的文献数")
    failed: list[FromSearchFailedItem] = Field(
        default_factory=list,
        description="处理失败的候选明细（单条异常，不影响其他候选）",
    )
    failedCount: int = Field(default=0, ge=0, description="处理失败的候选数，等于 failed.length")
    paperIds: list[int] = Field(
        default_factory=list,
        description="所有成功入库的 paper DB id（含 imported + skipped 两类）",
    )
    fulltextEligiblePaperIds: list[int] = Field(
        default_factory=list,
        description="本次成功入库/命中的、带 Sciverse doc_id 的 paper DB id",
    )


class SciverseSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseUrl: str | None = Field(default=None, max_length=2000)


class SciversePingResult(BaseModel):
    ok: bool
    baseUrl: str
    resultCount: int


class SciverseMetaSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=4096)
    filters: list[dict] = Field(default_factory=list, max_length=50)
    sort: list[dict] = Field(default_factory=list, max_length=10)
    fields: list[str] = Field(default_factory=list, max_length=80)
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4000)
    freshnessBoost: Literal["NONE", "MILD", "STRONG"] | None = None
    baseUrl: str | None = Field(default=None, max_length=2000)


class SciverseMetaSearchResult(BaseModel):
    candidates: list[dict]
    partial: bool = False
    partialReason: str | None = None
    totalCount: int | None = None
    page: int | None = None
    pageSize: int | None = None
    totalPages: int | None = None
    nextCursor: str | None = None
    searchTimeMs: float | None = None


class SciverseAgenticSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4096)
    topK: int = Field(default=10, ge=1, le=100)
    subQueries: int = Field(default=0, ge=0, le=10)
    baseUrl: str | None = Field(default=None, max_length=2000)


class SciverseAgenticSearchResult(BaseModel):
    hits: list[dict]


class SciverseFetchContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docId: str | None = Field(default=None, max_length=255)
    baseUrl: str | None = Field(default=None, max_length=2000)
    maxChars: int | None = Field(default=None, ge=1, le=1000000)


class SciverseFetchContentResult(BaseModel):
    paperId: int
    docId: str
    attachmentId: int
    chars: int
    sha256: str


class SciverseBackfillFulltextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paperIds: list[int] | None = Field(default=None, description="可选：只在这些 paper id 的交集内补全文")
    maxPapers: int = Field(default=50, ge=1, description="本次最多处理的候选论文数")
    excludePaperIds: list[int] | None = Field(
        default=None,
        description="可选：跳过这些 paper id（前端多轮循环时传已失败项，避免失败项挡住后续候选）",
    )


class SciverseBackfillFailedItem(BaseModel):
    paperId: int
    reason: str


class SciverseBackfillFulltextResult(BaseModel):
    total: int = Field(ge=0, description="本次条件下可补全文的论文总数")
    fetched: int = Field(ge=0, description="成功拉取并落库全文的论文数")
    failed: list[SciverseBackfillFailedItem] = Field(default_factory=list)
    skipped: int = Field(ge=0, description="因 maxPapers 上限本次未处理的论文数")
    remaining: int = Field(ge=0, description="仍有资格但本次未处理的论文数；>0 时可继续调用")


# --- P3-T1 元数据补全 ---

class BackfillMetadataRequest(BaseModel):
    """POST /projects/{pid}/papers/backfill-metadata 请求体。"""
    limit: int = Field(20, ge=1, le=100, description="最多处理的文献数（保配额）")
    onlyMissing: bool = Field(True, description="True=仅处理缺 abstract、creators 或 year 的篇；False=全量")


class BackfillMetadataResult(BaseModel):
    """POST /projects/{pid}/papers/backfill-metadata 响应体。"""
    processed: int = Field(ge=0, description="本次处理的文献总数")
    updated: int = Field(ge=0, description="成功回填（至少一个字段更新）的文献数")
    skipped: int = Field(ge=0, description="跳过（无 markdown/已完整/LLM 未给出新字段）的文献数")
    failed: int = Field(ge=0, description="处理失败（LLM 错误/JSON 解析失败/DB 错误）的文献数")
    available: int = Field(ge=0, description="处理后仍待处理的篇数（剩余）：满足 onlyMissing 条件、尚未回填的文献数，不受 limit 截断")


# --- P3-T3 结构化抽取 ---

class ExtractStructuredRequest(BaseModel):
    """POST /projects/{pid}/papers/extract-structured 请求体。"""
    limit: int = Field(15, ge=1, le=100, description="最多处理的文献数（保配额）")
    reextract: bool = Field(False, description="True=强制重新抽取（覆盖已有 extraction）；False=跳过已有")


class ExtractStructuredResult(BaseModel):
    """POST /projects/{pid}/papers/extract-structured 响应体。"""
    processed: int = Field(ge=0, description="本次处理的文献总数")
    extracted: int = Field(ge=0, description="成功抽取（新建或更新）的文献数")
    skipped: int = Field(ge=0, description="跳过（无 markdown/已有 extraction 且未强制重提取）的文献数")
    failed: int = Field(ge=0, description="处理失败（LLM 错误/JSON 解析失败/DB 错误）的文献数")
    available: int = Field(ge=0, description="处理后仍待处理的篇数（剩余）：OCR done 且尚无 extraction 的文献数，reextract=true 时为 OCR done 总数，不受 limit 截断")
    noFulltext: int = Field(default=0, ge=0, description="项目内被跳过的无全文附件（仅题录）文献数")
    summary: str | None = Field(default=None, description="面向用户的批处理说明")


# --- B2 文档结构视图（块/表格网格；B3 复用并加 StructureResponse 信封）---


class StructureBlock(BaseModel):
    block_idx: int            # content_list 中的块序号(0-based, 稳定 ID)
    type: str                 # "text" | "title" | "table" | "image"
    text_level: int | None    # 标题层级(1/2/3...); None=正文段落
    page_no: int              # 1-based PDF 页码 (content_list.page_idx + 1)
    md_line_start: int | None  # 该块在 full.md 中的起始行(1-based); None=无法精确行级定位(零伪造,前端降级到页/bbox)
    md_line_end: int | None    # 该块在 full.md 中的结束行(1-based, 含); None 同上
    bbox: list[float] | None  # [x0,y0,x1,y1]; 无坐标时 None
    section_title: str        # 该块所属最近标题
    text_preview: str         # 文本前 120 字(表/图为 caption 或占位)


class StructureCell(BaseModel):
    row: int                  # 展开后网格行(0-based)
    col: int                  # 展开后网格列(0-based)
    text: str                 # 单元格文本(逐字, 不改写)


class StructureTable(BaseModel):
    table_idx: int            # 第几张表(0-based)
    block_idx: int            # 对应的 content_list 块序号(用于定位/高亮)
    page_no: int
    bbox: list[float] | None
    n_rows: int
    n_cols: int
    grid: list[list[str]]     # 展开后的网格(colspan/rowspan 已处理)
    caption: str


class StructureResponse(BaseModel):
    """B3 结构化溯源端点信封：块视图 + 表格网格 + 溯源元数据（页/sha/坐标空间）。"""
    paper_id: int
    attachment_id: int
    page_count: int
    blocks: list[StructureBlock]
    tables: list[StructureTable]
    has_bbox: bool
    markdown_sha256: str | None        # full.md 内容 hash(≠ PDF sha256)
    schema_version: int = 1
    source_pdf_sha256: str | None = None
    bbox_coord_space: str | None = None
    page_width: float | None = None
    page_height: float | None = None
    rotation: int | None = None
