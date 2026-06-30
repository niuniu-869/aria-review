"""BiblioCN 安全带模块 — 阶段 4b

包含三个核心组件:
  evidence.py      — EvidenceRef: 将引用/论断绑定到语料真实记录
  citation.py      — check_citations_against_records: 引用存在性校验（Guardrails AI Validator + Guard 实现）
  guarded_stream.py — GuardedStream: 缓冲 token 流到句/章边界, 通过校验后放行

设计原则:
  - Agent 不能绕过: 任何最终输出都经 GuardedStream
  - 先缓冲再校验再放行: token 吐出后不可撤回
  - 伪造引用在放行前被拦/标记
  - 本模块完全独立, 不依赖 FastAPI / 数据库 / 真实 LLM Key
"""
from .evidence import EvidenceRef
from .citation import check_citations_against_records, CitationCheckResult
from .guarded_stream import GuardedStream, FabricatedCitationError

__all__ = [
    "EvidenceRef",
    "check_citations_against_records",
    "CitationCheckResult",
    "GuardedStream",
    "FabricatedCitationError",
]
