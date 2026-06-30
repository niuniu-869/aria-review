#!/usr/bin/env python
"""Task P2-5 — RunLog 验证器 CLI。

对 build_runlog 导出的 RunLog（runlog/v1）做离线可验证校验，逐项打印 OK/FAIL +
错误明细 + 最终裁决，退出码 0（通过）/ 1（失败）。

用法：
  services/agent/.venv/bin/python scripts/verify_runlog.py <runlog.json>
  services/agent/.venv/bin/python scripts/verify_runlog.py <runlog.json> \\
      --corpus-hashes <hashes.json>  # JSON 文件：sha256 字符串列表（或 {"hashes":[...]}）
  services/agent/.venv/bin/python scripts/verify_runlog.py <runlog.json> \\
      --max-fabricated 0

诚实声明：本校验器证明结构完整 + 哈希链自洽 + 引用溯源（给语料哈希时）+ 零伪造计数；
哈希链自洽 != 防篡改——真正防篡改需把 chain_head 外部锚定（不可变存储 / 签名），不在范围内。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 把 services/agent 加入 sys.path（脚本直接运行时需要）
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _SCRIPT_DIR.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from app.agent.runlog_verify import verify_runlog  # noqa: E402


def _load_corpus_hashes(path: str) -> set[str]:
    """从 JSON 文件读语料哈希集合；支持纯 list 或 {"hashes": [...]} 两种结构。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("hashes", [])
    return {str(h) for h in data}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="验证 BiblioCN agent RunLog（runlog/v1）",
    )
    parser.add_argument("runlog", help="RunLog JSON 文件路径")
    parser.add_argument(
        "--corpus-hashes",
        default=None,
        help="语料内容哈希 JSON 文件（list[str] 或 {\"hashes\":[...]}），给定则启用引用溯源校验",
    )
    parser.add_argument(
        "--max-fabricated",
        type=int,
        default=0,
        help="允许的最大伪造引用数（默认 0 = 零容忍）",
    )
    args = parser.parse_args(argv)

    runlog = json.loads(Path(args.runlog).read_text(encoding="utf-8"))
    corpus_hashes = (
        _load_corpus_hashes(args.corpus_hashes) if args.corpus_hashes else None
    )

    report = verify_runlog(
        runlog,
        corpus_content_hashes=corpus_hashes,
        max_fabricated=args.max_fabricated,
    )

    print("=" * 60)
    print(f"RunLog 校验: {args.runlog}")
    print("=" * 60)
    for name, passed in report.checks.items():
        status = "OK  " if passed else "FAIL"
        print(f"  [{status}] {name}")

    if report.errors:
        print("\n明细 (errors / info):")
        for err in report.errors:
            print(f"  - {err}")

    print("-" * 60)
    verdict = "PASS" if report.ok else "FAIL"
    print(f"最终裁决: {verdict}")
    print("=" * 60)

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
