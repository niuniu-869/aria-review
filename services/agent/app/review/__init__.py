"""全文综述生成引擎 — 阶段 5-2b

包含：
  read.py      — 阅读 subagent（map）：summarize_paper / summarize_papers
  synthesis.py — 综述合成（reduce）：generate_review（流式 + GuardedStream）
"""
from .read import (
    summarize_paper,
    summarize_papers,
    PaperSummary,
    KeyPoint,
)
from .synthesis import generate_review, ReviewEvent

__all__ = [
    "summarize_paper",
    "summarize_papers",
    "PaperSummary",
    "KeyPoint",
    "generate_review",
    "ReviewEvent",
]
