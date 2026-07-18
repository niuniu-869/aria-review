"""三层领域模型 Library→Project→Corpus (spec §4)。schema 对齐 CSL-JSON。

11 张表：
  Library 层: paper, tag, paper_tag, note, attachment
  Project 层: project, project_paper, draft, agent_run
  Corpus 层:  corpus, corpus_paper
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _pk() -> Mapped[int]:
    return mapped_column(Integer, primary_key=True, autoincrement=True)


# ---------------------------------------------------------------------------
# Library 层
# ---------------------------------------------------------------------------

class Paper(Base):
    """文献题录（Library 核心实体）。字段对齐 CSL-JSON。"""
    __tablename__ = "paper"

    id: Mapped[int] = _pk()
    item_type: Mapped[str] = mapped_column(String(40), default="journalArticle")
    title: Mapped[str] = mapped_column(Text)
    creators: Mapped[list | None] = mapped_column(JSON, default=list)  # CSL creators 数组
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    container_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    volume: Mapped[str | None] = mapped_column(String(32), nullable=True)
    issue: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pages: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str | None] = mapped_column(
        String(40), nullable=True)  # wos/openalex/arxiv/upload
    csl_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(255), index=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # 预留多租户
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("dedup_key", "owner_id", name="uq_paper_dedup"),
        # 当 owner_id IS NULL 时，PostgreSQL 的复合唯一约束不强制（NULL≠NULL），
        # 需要额外的部分唯一索引来保证 dedup_key 在无主人时全局唯一。
        Index(
            "uq_paper_dedup_null_owner",
            "dedup_key",
            unique=True,
            postgresql_where=text("owner_id IS NULL"),
        ),
    )


class Tag(Base):
    """标签（多对多挂 Paper）。"""
    __tablename__ = "tag"

    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(String(80), unique=True)


class PaperTag(Base):
    """Paper ↔ Tag 关联表。"""
    __tablename__ = "paper_tag"

    paper_id: Mapped[int] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True)


class PaperExternalId(Base):
    """外部文献源标识。保存 provider 的稳定 ID，避免覆盖 Paper.source。"""
    __tablename__ = "paper_external_id"

    id: Mapped[int] = _pk()
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    id_type: Mapped[str] = mapped_column(String(40), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        # per-paper 唯一:不用全局 (provider,id_type,external_id) 唯一 —— 全局唯一会让
        # 重复文献的第二篇 paper upsert 被静默跳过且 select 空返回(codex Batch2 P1);
        # paper 级去重由 Paper.dedup_key 负责,external_id 只需 per-paper 唯一。
        UniqueConstraint(
            "paper_id", "provider", "id_type", "external_id",
            name="uq_paper_external_id_paper",
        ),
    )


class Note(Base):
    """研究笔记（可挂 paper 或 project）。"""
    __tablename__ = "note"

    id: Mapped[int] = _pk()
    paper_id: Mapped[int | None] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class Attachment(Base):
    """附件（PDF 路径/URL + MinerU 状态占位）。"""
    __tablename__ = "attachment"

    id: Mapped[int] = _pk()
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"))
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mineru_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True)  # 第③组 OCR 占位: pending/processing/done/failed
    markdown_path: Mapped[str | None] = mapped_column(
        Text, nullable=True)  # 阶段5-1: MinerU 解析后的 Markdown 存盘路径


# ---------------------------------------------------------------------------
# Project 层
# ---------------------------------------------------------------------------

class Project(Base):
    """研究项目（组织 Paper 进行文献综述）。"""
    __tablename__ = "project"

    id: Mapped[int] = _pk()
    name: Mapped[str] = mapped_column(String(255))
    research_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    # M2 (codex P0): name 唯一，使 project__create 借业务唯一约束实现幂等。
    __table_args__ = (
        UniqueConstraint("name", name="uq_project_name"),
    )


class ProjectPaper(Base):
    """Project 与 Paper 的关联（含筛选状态）。"""
    __tablename__ = "project_paper"

    id: Mapped[int] = _pk()
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"), index=True)
    inclusion_status: Mapped[str] = mapped_column(
        String(16), default="candidate")  # candidate / included / excluded / maybe
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    screening_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    screening_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by: Mapped[str] = mapped_column(
        String(8), default="user")  # user / agent
    order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("project_id", "paper_id", name="uq_project_paper"),
    )


class Draft(Base):
    """综述草稿（Project 下可有多版）。"""
    __tablename__ = "draft"

    id: Mapped[int] = _pk()
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(
        String(16), default="review")  # outline / review
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class AiJob(Base):
    """AI 生成任务快照。

    用于让综述、语料对话和短文本工具在刷新/跳转后恢复状态与结果。
    API key 只在请求内存中使用，不写入 request_json。
    """
    __tablename__ = "ai_job"

    id: Mapped[int] = _pk()
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True)
    corpus_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # review/chat/summary/translate/rewrite
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    request_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_text: Mapped[str] = mapped_column(Text, default="")
    annotated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    events_json: Mapped[list | None] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class GapCandidateRecord(Base):
    """研究方向 GAP 候选落库（A1 scratchpad 持久化 + A5 verify/HITL）。

    单表承载三种读取口径：
      - scratchpad：按 run_id 取本次 discover run 的全部条目（实时 HITL 视图）。
      - verdict：按 gap_id 取单条价值裁决。
      - HITL：按 gap_id 改 status / statement。
    run_id = discover 异步 run 标识（= str(ai_job.id)）；gap_id 全局唯一（服务端 uuid）。
    """
    __tablename__ = "gap_candidate"

    id: Mapped[int] = _pk()
    gap_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True, index=True)
    theme: Mapped[str] = mapped_column(Text, default="")
    statement: Mapped[str] = mapped_column(Text, default="")
    lens: Mapped[str] = mapped_column(String(16), default="concept")  # concept/method/theory
    supporting_papers: Mapped[list | None] = mapped_column(JSON, default=list)
    counter_evidence: Mapped[list | None] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", index=True)  # draft/verified/accepted/rejected
    value_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_pack: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # P2 feasibility-scout：与 novelty 解耦的可行性裁决 + 攒证包（nullable=旧行无此列）。
    feasibility_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    feasibility_pack: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    """Agent 运行记录（每次 harness 执行一条）。"""
    __tablename__ = "agent_run"

    id: Mapped[int] = _pk()
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    # P0 三入口隔离：本 run 所属入口（search/review/gap）；NULL = legacy 全工具入口。
    # 供 tool_ids/system_prompt 收窄 + 对话历史按 entry 隔离（list_recent_dialog 过滤）。
    entry: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    messages_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)
    rounds_log: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), default="running")  # running / paused / done / failed / awaiting_confirmation
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    auto_confirm: Mapped[bool] = mapped_column(Boolean, default=False)
    final_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 修复6 (codex P2-5): 实际存 list（state.all_tool_results），类型与存储/schema 对齐。
    evidence_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pending_round: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # M2: 工具/产出校验汇总（Guardrails/一致性校验结果快照），nullable=旧 run 无此列。
    validation_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class AgentEvent(Base):
    """Agent 运行时事件流（有序、不可变追加日志）。"""
    __tablename__ = "agent_event"

    id: Mapped[int] = _pk()
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    # M2 哈希链：prev_hash=上一条 event_hash，event_hash=本条摘要（含 ts）。
    # 均 nullable：迁移在既有行上成功；校验器把 null 链视作 legacy（旧 append_event 不填）。
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_agent_event_seq"),
    )


class ToolInvocation(Base):
    """工具调用幂等日志（用于重放保护和结果缓存）。"""
    __tablename__ = "tool_invocation"

    id: Mapped[int] = _pk()
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "idempotency_key", name="uq_tool_invocation_key"),
    )


# ---------------------------------------------------------------------------
# Corpus 层
# ---------------------------------------------------------------------------

class Corpus(Base):
    """冻结语料（Project included 论文集的不可变快照，送 R 计量）。"""
    __tablename__ = "corpus"

    id: Mapped[int] = _pk()
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(
        String(12), default="parsing")  # parsing / ready / failed
    document_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dbsource: Mapped[str | None] = mapped_column(String(20), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    r_corpus_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("project_id", "content_hash", name="uq_corpus_hash"),
    )


class CorpusPaper(Base):
    """冻结成员快照：锁定某次 Corpus 的论文集合、排序、当时题录（不可变）。"""
    __tablename__ = "corpus_paper"

    id: Mapped[int] = _pk()
    corpus_id: Mapped[int] = mapped_column(
        ForeignKey("corpus.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("paper.id"), index=True)  # 不 CASCADE：paper 删除后仍保留快照引用
    order: Mapped[int] = mapped_column(Integer, default=0)
    inclusion_status_snapshot: Mapped[str] = mapped_column(String(16))
    record_hash: Mapped[str] = mapped_column(String(64))
    csl_json_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("corpus_id", "paper_id", name="uq_corpus_paper"),
    )


# ---------------------------------------------------------------------------
# W5-b 结构化抽取层
# ---------------------------------------------------------------------------

class PaperExtraction(Base):
    """结构化抽取结果（W5-b 元索引）。每篇 paper 至多一条（unique paper_id）。"""
    __tablename__ = "paper_extraction"

    id: Mapped[int] = _pk()
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    research_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str | None] = mapped_column(Text, nullable=True)
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset: Mapped[str | None] = mapped_column(Text, nullable=True)
    contribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # LLM 原始返回备份
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


# ---------------------------------------------------------------------------
# M4 工件层
# ---------------------------------------------------------------------------

class Artifact(Base):
    """Agent 产出工件（综述/分析/抽取/文献集）的持久化身份索引。

    内容本身派生自 RunLog（不可变审计源）；此表只持久化身份/pin/标注/排序，
    使工件可跨会话恢复（AgentChat 新 run 清空 events，纯前端投影无法恢复）。

    字段说明：
      - type          : 工件类型 review|analysis|extraction|paperset
      - title         : 用户可改的展示标题（可从 final_output 首行自动提取）
      - run_id        : 派生自哪次 AgentRun（可 null：手动创建或从 ReviewPanel 产出）
      - source_event_seq : 对应 AgentEvent.seq（定位 run_complete 等具体事件）
      - content_ref   : 内容定位符（如 "runlog:rid" 或 "draft:did"，供前端取内容）
      - pinned        : 是否置顶（UI 工件栏优先展示）
      - user_annotation : 用户标注（富文本备注，可 null）
      - order         : 同 project 下排序（前端可拖拽调序）
    """
    __tablename__ = "artifact"

    id: Mapped[int] = _pk()
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(
        String(32), default="review")  # review|analysis|extraction|paperset
    title: Mapped[str] = mapped_column(Text, default="")
    source_event_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    user_annotation: Mapped[str | None] = mapped_column(Text, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


# ---------------------------------------------------------------------------
# B1 可信溯源层：文档结构（MinerU content_list + 行号↔页码映射 + 块行区间）
# ---------------------------------------------------------------------------

class DocumentStructure(Base):
    """文档结构快照：摄取期捕获 MinerU content_list（结构+page_idx+bbox），

    并落库行号↔页码映射（build_line_page_map）与块→行区间（build_block_line_ranges），
    使后续综述/引用能把每个数字/论断溯源到源文档的精确页/段/表。

    与 Attachment 一对一（attachment_id unique）；某附件重新摄取时 upsert 而非堆叠。
    bbox 标定字段（坐标空间/页宽高/旋转）留待后续任务，本表先记录坐标空间标识。
    """
    __tablename__ = "document_structure"

    id: Mapped[int] = _pk()
    attachment_id: Mapped[int] = mapped_column(
        ForeignKey("attachment.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    content_list: Mapped[list | None] = mapped_column(JSON, nullable=True)
    page_map: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # build_line_page_map 输出
    block_line_ranges: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # build_block_line_ranges 输出
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    has_bbox: Mapped[bool] = mapped_column(Boolean, default=False)
    markdown_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True)  # full.md 内容的 sha256（非 PDF）
    source_pdf_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True)  # PDF 的 sha256（= Attachment.sha256）
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    # bbox 标定字段（v2 §5.3）：MinerU bbox 为 0–1000 归一坐标，page_width/height/rotation
    # 留待后续标定任务填充。
    bbox_coord_space: Mapped[str | None] = mapped_column(String(20), nullable=True)
    page_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_height: Mapped[float | None] = mapped_column(Float, nullable=True)
    rotation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


# ---------------------------------------------------------------------------
# 认证与多租户层（Phase B）：用户 / 会话 / 邀请码 / 积分计费
# ---------------------------------------------------------------------------

class User(Base):
    """平台用户（邮箱 + 密码 + 邀请码注册）。

    表名 app_user 避开 PostgreSQL 保留字 user（raw SQL 无需引号）。
    credits 为积分余额缓存，真值以 credit_ledger 流水为准（同事务保证一致）。
    encrypted_keys: BYOK 用户 API key，Fernet 加密后存储，明文永不落库。
    """
    __tablename__ = "app_user"

    id: Mapped[int] = _pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True)  # OAuth-only 用户为 null（Phase C）
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="user")  # user / admin
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / disabled
    credits: Mapped[int] = mapped_column(Integer, default=0)  # 积分余额缓存（真值见 credit_ledger）
    encrypted_keys: Mapped[dict | None] = mapped_column(
        JSON, nullable=True)  # BYOK: Fernet 密文，{provider: token}
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now())


class AuthSession(Base):
    """服务端会话（httpOnly cookie 承载随机 token；DB 只存 sha256(token)，不存明文）。"""
    __tablename__ = "auth_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(token) 十六进制
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(index=True)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 排查异常，非明文 IP
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class InviteCode(Base):
    """注册邀请码（Claude Code 运维发放，无管理页面）。used_by 非空即已用。"""
    __tablename__ = "invite_code"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    used_by: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    used_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 批次/备注
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class RedeemCode(Base):
    """积分兑换码（充值积分；Claude Code 运维发放）。并发同码只允许兑一次。"""
    __tablename__ = "redeem_code"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    credits: Mapped[int] = mapped_column(Integer)  # 面值（>0，见 CheckConstraint）
    # used_at 是「是否已用」的权威判据（不可回退）；used_by 仅审计——因 ondelete=SET NULL，
    # 删用户后 used_by 会变 NULL，若以 used_by 判据会让码复活（codex 二审 P1）。
    used_by: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    used_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 批次/备注
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("credits > 0", name="ck_redeem_code_credits_positive"),
    )


class CreditLedger(Base):
    """积分流水（可审计；余额 = SUM(delta)，与 User.credits 同事务保持一致）。"""
    __tablename__ = "credit_ledger"

    id: Mapped[int] = _pk()
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    delta: Mapped[int] = mapped_column(Integer)  # +充值 / -消耗 / ±人工调账
    reason: Mapped[str] = mapped_column(String(24))  # redeem / consume / adjust / refund
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # code / ai_job.id
    balance_after: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        # 幂等护栏：同一 (user, reason, ref) 至多一条（ref 非空时），防重复扣费/退款
        # 静默送钱或双扣（codex 二审 P1）。ref 为空的运维调账(adjust)不受约束。
        Index("uq_credit_ledger_idempotent", "user_id", "reason", "ref",
              unique=True, postgresql_where=text("ref IS NOT NULL")),
    )


# ---------------------------------------------------------------------------
# 遥测层（0.6.1 P0：漏斗观测埋点）
# ---------------------------------------------------------------------------

class AnalyticsEvent(Base):
    """产品埋点事件（只增不改的观测日志）。

    0.6.1 P0：把"综述可达性是否是瓶颈"从猜测变成可查数据。记录 review 漏斗关键点
    （面板曝光 / precheck 阻断原因 / 生成点击 / job 成功失败）。best-effort，不入
    主业务事务，写失败不影响主流程。分析经直连 SQL 按 event/created_at 聚合。
    """
    __tablename__ = "analytics_event"

    id: Mapped[int] = _pk()
    # 删用户/项目不应删审计事件的历史计数；user 置空、project 级联删（项目没了埋点无意义）。
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        server_default=func.now(), index=True)
