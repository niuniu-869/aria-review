#!/usr/bin/env python
"""P3-4 — grounding 质量指标评估 CLI。

从 RunLog JSON（build_runlog 产出，schema=runlog/v1）计算 grounding 准确率、
溯源命中率、零伪造率等硬数字，输出 metrics.json 和 metrics.md，供技术报告引用。

用法示例
--------
# 最简调用（不带语料哈希，provenance_hit_rate=null）：
  python scripts/eval_grounding.py --runlog /tmp/agent_runlog.json

# 带语料哈希文件（每行一个 sha256）：
  python scripts/eval_grounding.py \\
      --runlog /tmp/agent_runlog.json \\
      --corpus-hashes /tmp/corpus_hashes.txt \\
      --out-json /tmp/metrics.json \\
      --out-md   /tmp/metrics.md

语料哈希来源（二选一）
  1. --corpus-hashes <file>：纯文本，每行一个 sha256 hex 字符串。
  2. 不传：provenance_hit_rate 跳过（结果中为 null），其余指标正常计算。

退出码：0（成功），1（RunLog 读取/解析失败）。
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

from app.agent.metrics import grounding_metrics  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _load_corpus_hashes(path: str) -> set[str]:
    """从纯文本文件读语料哈希集合（每行一个 sha256）。

    空行和 # 开头的注释行自动跳过。
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return {
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }


def _fmt_float(val: float | None) -> str:
    """将浮点数格式化为百分比字符串（None → 'N/A'）。"""
    if val is None:
        return "N/A"
    return f"{val:.4f} ({val * 100:.2f}%)"


def _build_markdown(metrics: dict, runlog_path: str) -> str:
    """将 metrics dict 渲染为 Markdown 表格，供技术报告直接引用。"""
    insufficient = metrics.get("insufficient_evidence")
    lines = [
        f"# BiblioCN Grounding 质量指标报告",
        f"",
        f"**RunLog**: `{runlog_path}`",
        f"",
    ]
    # codex P1：无引用 → 不可评分，醒目标注，绝不伪装满分
    if insufficient:
        lines += [
            "> ⚠️ **不可评分（insufficient_evidence）**：本 run 无任何引用"
            "（green+yellow+fabricated=0），各率为 N/A。空 review 不计为满分。",
            "",
        ]
    lines += [
        "| 指标 | 值 |",
        "|------|----|",
        f"| grounding_accuracy（grounding 准确率）| {_fmt_float(metrics.get('grounding_accuracy'))} |",
        f"| provenance_hit_rate（溯源命中率）| {_fmt_float(metrics.get('provenance_hit_rate'))} |",
        f"| zero_fabrication_rate（零伪造率）| {_fmt_float(metrics.get('zero_fabrication_rate'))} |",
        f"| scoreable（可评分）| {metrics.get('scoreable')} |",
        f"| evidence_count（有效证据引用数）| {metrics.get('evidence_count')} |",
        f"| fabricated_count（伪造引用数）| {metrics.get('fabricated_count')} |",
        f"| green_count（强命中 green）| {metrics.get('green_count')} |",
        f"| yellow_count（弱命中 yellow）| {metrics.get('yellow_count')} |",
        f"",
        "> 注：provenance_hit_rate 为 N/A 可能因未提供语料哈希（无法判定）或无引用（不可评分）。",
        "> 无任何引用时各率为 N/A 且 scoreable=False（空 review 不伪装为满分）。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="BiblioCN P3-4：从 RunLog 计算 grounding 质量指标",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--runlog",
        default="/tmp/agent_runlog.json",
        help="RunLog JSON 文件路径（build_runlog 产出，schema=runlog/v1）。默认 /tmp/agent_runlog.json",
    )
    parser.add_argument(
        "--corpus-hashes",
        default=None,
        help=(
            "语料内容哈希文本文件（每行一个 source_content_sha256 hex 字符串）。"
            "不传则 provenance_hit_rate 返回 null（无法判定）。"
        ),
    )
    parser.add_argument(
        "--out-json",
        default=None,
        help="metrics 输出 JSON 文件路径（不传则只打印到 stdout）",
    )
    parser.add_argument(
        "--out-md",
        default=None,
        help="metrics 输出 Markdown 文件路径（不传则只打印到 stdout）",
    )
    args = parser.parse_args(argv)

    # ---- 读取 RunLog ----
    runlog_path = args.runlog
    try:
        runlog = json.loads(Path(runlog_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[ERROR] RunLog 文件不存在：{runlog_path}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"[ERROR] RunLog JSON 解析失败：{exc}", file=sys.stderr)
        return 1

    # ---- 读取语料哈希（可选）----
    corpus_hashes: set[str] | None = None
    if args.corpus_hashes:
        try:
            corpus_hashes = _load_corpus_hashes(args.corpus_hashes)
            print(f"[INFO] 已加载语料哈希 {len(corpus_hashes)} 条：{args.corpus_hashes}")
        except FileNotFoundError:
            print(f"[ERROR] 语料哈希文件不存在：{args.corpus_hashes}", file=sys.stderr)
            return 1

    # ---- 计算指标 ----
    metrics = grounding_metrics(runlog, corpus_hashes=corpus_hashes)

    # ---- 输出 JSON ----
    metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2, default=str)
    print("\n=== metrics.json ===")
    print(metrics_json)

    if args.out_json:
        Path(args.out_json).write_text(metrics_json, encoding="utf-8")
        print(f"[INFO] metrics.json 已写入：{args.out_json}")

    # ---- 输出 Markdown ----
    md_text = _build_markdown(metrics, runlog_path)
    print("\n=== metrics.md ===")
    print(md_text)

    if args.out_md:
        Path(args.out_md).write_text(md_text, encoding="utf-8")
        print(f"[INFO] metrics.md 已写入：{args.out_md}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
