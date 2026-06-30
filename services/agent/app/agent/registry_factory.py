"""registry_factory — 组装 agent 工作台默认 ToolRegistry。

把 Library / Project / Corpus / Analysis 四个领域工具按真实构造签名注册进一个
ToolRegistry，并标记写工具（library / project / corpus 串行执行；analysis 只读）。

构造签名（已对齐 app/tools/*.py）：
  LibraryTool(session_factory)
  ProjectTool(session_factory)
  CorpusTool(session_factory, r_client)
  AnalysisTool(session_factory, r_client)
"""
from __future__ import annotations

from typing import Any, Callable

from ..harness.tools import ToolRegistry
from ..tools.analysis import AnalysisTool
from ..tools.corpus import CorpusTool
from ..tools.extract import ExtractTool
from ..tools.ingest import IngestTool
from ..tools.library import LibraryTool
from ..tools.project import ProjectTool
from ..tools.read_paper import ReadPaperTool
from ..tools.review_tool import ReviewTool
from ..tools.scratchpad import ScratchpadTool
from ..tools.search import SearchTool
from ..tools.submit_evidence_pack import SubmitEvidencePackTool


def build_registry(session_factory: Callable, r_client: Any) -> ToolRegistry:
    """构建并返回包含四个领域工具的 ToolRegistry。

    Args:
        session_factory: async_sessionmaker，供工具开会话访问 DB。
        r_client: r-analysis 客户端，供 corpus / analysis 工具调用 R 服务。
    """
    reg = ToolRegistry()
    reg.register(LibraryTool(session_factory))
    reg.register(ProjectTool(session_factory))
    reg.register(CorpusTool(session_factory, r_client))
    reg.register(AnalysisTool(session_factory, r_client))
    # P3-2: 综述工具（安全带强制；非写工具，不需确认 gate）。
    # session_factory 为兜底注入；execute 时优先用 tool_context 的 session_factory/emit/state。
    reg.register(ReviewTool(session_factory))
    # P2-T2: 检索工具（只读，调 /search/openalex，不建库；不进 mark_write_tools）。
    reg.register(SearchTool(r_client))
    # P0-1: 文档处理工具化 —— 全文摄取（MinerU 解析）+ 结构化抽取/元数据回填。
    # 二者均写库（IngestTool 写 Paper/Attachment/ProjectPaper；ExtractTool 写
    # PaperExtraction/Paper）→ 标写工具串行执行。session_factory 为兜底注入，
    # execute 时优先用 tool_context 的 session_factory/override。
    reg.register(IngestTool(session_factory))
    reg.register(ExtractTool(session_factory))
    # 研究副驾工具（A1/A2）：read_paper 按需导航单篇论文（只读）；scratchpad GAP 工作
    # 记忆（写库 gap_candidate）。二者构造无参，运行期从 tool_context 取 run_id/
    # session_factory/papers。进入工具池后，GAP/价值 subagent 经 skill.tool_ids 最小授权
    # 选择子集（A3）；主对话 agent (tool_ids=None) 也可调用（read_paper 利于按需取证）。
    reg.register(ReadPaperTool())
    reg.register(ScratchpadTool())
    # A4: 价值核验证据提交（collect-only，不裁决）。写工具（标 write 串行）。value-evidence
    # subagent 经 tool_ids 选择它；裁决由 app/review/value_check.py 确定性 resolver 出。
    reg.register(SubmitEvidencePackTool())
    # library / project / corpus / ingest / extract / scratchpad / submit_evidence_pack 含写操作
    # → 串行执行；analysis / search / read_paper 只读。（这些工具的 tags 已含 "write"，register
    # 会自动标记；此处显式声明以防 tags 变更。）
    reg.mark_write_tools(
        "library", "project", "corpus", "ingest", "extract",
        "scratchpad", "submit_evidence_pack",
    )
    return reg
