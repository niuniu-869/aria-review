#!/usr/bin/env python
"""作战方案 P0-2 — 零伪造闭环 demo（离线、零伪造、口径收窄，可现场/录屏）。

目标（§4 P0-2 + §10.3）：用一个完全离线、可复现的脚本演示"生成 → 审计 → 独立验证"
三段闭环，且 claim 严格收窄到代码能证明的边界——不夸大。

三段（口径严格按 §10.3）：
  正常路径
    - 跑一份预置综述，所有引用全部命中预置语料（绿/黄）。
    - 产出 runlog（真实哈希链 + content_sha256）。
    - verify_runlog 带 --corpus-hashes、--max-fabricated 0 → PASS。

  检出路径（默认 ANNOTATE）★口径核心：
    - 同一份综述里注入 1 条语料中不存在的引用。
    - citation check（确定性代码反查 records，非 LLM 自评）把它判红、标红、
      计入 validation_summary.fabricated_*。
    - 注意：默认行为是"检出标红 + 计入日志，继续输出整份"，**不是拒绝整份**。
    - 产出 runlog。
    - --max-fabricated 0 → FAIL（命中 1 条伪造，超过零容忍上限）。
    - --max-fabricated 1 → PASS（阈值可配；比口号式"零伪造率下降"更可信）。

  阻断路径（显式 CitationFailStrategy.REJECT）：
    - 同样注入 1 条伪造引用，但策略显式设为 REJECT → 整份拒绝（抛错），
      区别于上面的 ANNOTATE。这条路径才是"阻断整份"，必须显式声明，不是默认。

口径铁律（§10.3，全程不破）：
  - 默认是"检出标红 + 计入日志"，不是"拒绝整份"；要"阻断"必须显式 REJECT。
  - verify 必带 --corpus-hashes，否则不启用 evidence_traceable 溯源校验。
  - 不说"哈希链防篡改"。准确说："离线重算证明日志内部自洽；真正防篡改需把
    chain_head 外部锚定（不可变存储 / 签名 / 时间戳服务），不在本脚本范围。"
  - citation check 是确定性代码反查 records（app/cite_check.py），不是 LLM 自评分。
  - 本脚本不验 final_output 的语义/逻辑正确性；只验"引用存在性 + 溯源哈希 + 日志自洽"。

离线性（§10.2-7）：
  - 不依赖现场真实 OCR（MinerU）/真实 LLM。综述文本、语料 records、content_sha256
    全部预置在本脚本内（模拟"缓存命中 / 预置 markdown"），跑起来零外部网络。
  - 哈希链是真造的：经 append_event_chained 落库 + build_runlog 聚合（同测试同路径）。
  - 校验是真跑的：直接以子进程调用 scripts/verify_runlog.py（对外演示的同一命令）。

依赖：本脚本需要一个可连的 Postgres 测试库（TEST_DATABASE_URL，从 settings 读，
  默认与 agent 测试同库 bibliocn_test）。脚本自建表（create_all）+ 跑完 drop_all 清理，
  不新增数据库表（复用既有 ORM 模型）。
  drop_all 安全闸：跑前断言 TEST_DATABASE_URL 含 'test' 且 != 开发库 DATABASE_URL，
  否则拒绝运行——防误配把开发库表删掉。

用法：
  services/agent/.venv/bin/python scripts/demo_zero_fabrication.py
  services/agent/.venv/bin/python scripts/demo_zero_fabrication.py --out-dir /tmp/zf_demo --keep
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 把 services/agent 加入 sys.path（脚本直接运行时需要）
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _SCRIPT_DIR.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402 — 注册 ORM 映射
from app.agent.runlog import build_runlog  # noqa: E402
from app.repositories.agent_run import (  # noqa: E402
    append_event_chained,
    create_run,
    save_state,
)
from app.repositories.project import create_project  # noqa: E402
from app.safety.citation import (  # noqa: E402
    CitationFailStrategy,
    check_citations_against_records,
)
from app.harness.engine import LoopState  # noqa: E402


# ===========================================================================
# 预置语料（离线，模拟"缓存命中 / 预置 markdown"，不依赖现场 OCR/LLM）
# ===========================================================================
def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 三篇预置全文 markdown（极简，仅用于稳定算 content_sha256；真实场景由 MinerU 产出）
_FULLTEXT = {
    1: "# Bibliometric analysis of trustworthy data agents\n\nAria & Cuccurullo (2017) ...",
    2: "# Science mapping for systematic reviews\n\nSmith (2020) presents a method ...",
    3: "# Hallucination detection in LLM corpora\n\nChen et al. (2021) PMID 35012345 ...",
}

# records：r-analysis /records 同结构（idx/title/authors/year/doi/pmid），
# 另带 paper_id + content_sha256（项目加载函数会填充；此处预置以离线）。
CORPUS_RECORDS: list[dict] = [
    {
        "idx": 1,
        "paper_id": 101,
        "title": "Bibliometric analysis of trustworthy data agents",
        "authors": "ARIA M;CUCCURULLO C",
        "year": 2017,
        "doi": "10.1016/j.joi.2017.08.007",
        "content_sha256": _sha256(_FULLTEXT[1]),
    },
    {
        "idx": 2,
        "paper_id": 102,
        "title": "Science mapping for systematic reviews",
        "authors": "SMITH J",
        "year": 2020,
        "doi": "10.1000/sciencemap.2020",
        "content_sha256": _sha256(_FULLTEXT[2]),
    },
    {
        "idx": 3,
        "paper_id": 103,
        "title": "Hallucination detection in LLM corpora",
        "authors": "CHEN L",
        "year": 2021,
        "pmid": "35012345",
        "content_sha256": _sha256(_FULLTEXT[3]),
    },
]

# 语料内容哈希集合（verify 的 --corpus-hashes 输入；evidence_traceable 据此溯源）
CORPUS_HASHES: list[str] = [r["content_sha256"] for r in CORPUS_RECORDS]


# ===========================================================================
# 预置综述文本（离线，模拟一次"已生成"的综述，不打真实 LLM）
# ===========================================================================
# 正常综述：3 条引用全部命中预置语料
#   - DOI 10.1016/j.joi.2017.08.007 → green（精确命中 #1）
#   - Smith (2020) → yellow（作者+年命中 #2）
#   - PMID: 35012345 → green（精确命中 #3）
REVIEW_CLEAN = (
    "## 引言\n\n"
    "可信数据 Agent 的语料生产是大模型数据生态的关键环节。文献计量方法"
    "为系统梳理提供了基础 (10.1016/j.joi.2017.08.007)。\n\n"
    "## 方法\n\n"
    "系统综述常用科学知识图谱进行主题归纳 Smith (2020)，并辅以引用网络分析。\n\n"
    "## 防幻觉\n\n"
    "针对 LLM 语料的幻觉检测是近年热点 PMID: 35012345，可显著降低数据污染风险。\n"
)

# 注入伪造引用：在正常综述基础上多一条语料中不存在的 DOI
#   - 10.9999/fabricated.2099 → red（语料无此 DOI）→ fabricated
_FAKE_DOI = "10.9999/fabricated.2099"
REVIEW_INJECTED = REVIEW_CLEAN + (
    "\n## 一条被注入的伪造引用\n\n"
    f"另有研究声称提出了全新框架 ({_FAKE_DOI})，但该文献在语料中并不存在。\n"
)


# ===========================================================================
# 构建一份真实 runlog（真实哈希链 + content_sha256），落库后聚合再清理
# ===========================================================================
async def _build_one_runlog(
    factory: async_sessionmaker,
    *,
    review_text: str,
    strategy: str,
    prompt: str,
    label: str,
) -> tuple[dict, dict]:
    """跑一段引用校验 + 造真实哈希链 runlog。

    返回 (runlog_dict, check_info)。
    check_info 含 summary / fabricated / evidence_count，供脚本打印分镜摘要。

    strategy=REJECT 且含伪造引用时，check_citations_against_records 会抛 ValueError——
    这正是"阻断整份"路径，由调用方捕获并据此造一条 status=error 的 runlog。
    """
    async with factory() as s:
        # label + 随机后缀保证项目名唯一（project.name 有唯一约束 uq_project_name），
        # 也避免 --keep 后再次运行时撞名。
        suffix = os.urandom(4).hex()
        project = await create_project(s, {"name": f"zf_demo_{label}_{suffix}"})
        run = await create_run(s, project_id=project.id, plan="zero-fabrication demo")

        # 1) 记录 agent 处理过程事件（真实哈希链：append_event_chained 逐条链式落库）
        await append_event_chained(s, run.id, "run_start", {"prompt": prompt})
        await append_event_chained(
            s, run.id, "tool_call",
            {"tool_id": "review", "action": "generate", "strategy": strategy},
        )

        # 2) 确定性引用校验（反查 records，非 LLM 自评）
        rejected = False
        reject_msg = ""
        try:
            result = check_citations_against_records(
                review_text,
                CORPUS_RECORDS,
                strategy=strategy,
                corpus_id="zf_demo_corpus",
            )
        except ValueError as e:
            # 仅 REJECT 策略命中伪造才会抛 ValueError → 整份拒绝。
            # codex：except 不收窄会把未来其他 ValueError 误判为"拒绝"，故只在 REJECT 下吞并，
            # 其余策略原样 re-raise（暴露真问题，不掩盖）。
            if strategy != CitationFailStrategy.REJECT:
                raise
            rejected = True
            reject_msg = str(e)
            # 仍跑一次 NOOP 仅为拿到 summary/fabricated 计数 + annotated 标红文本
            # （不改变"整份拒绝"这一事实）。
            result = check_citations_against_records(
                review_text,
                CORPUS_RECORDS,
                strategy=CitationFailStrategy.NOOP,
                corpus_id="zf_demo_corpus",
            )

        fabricated = list(result.fabricated)
        evidence_refs = [ref.to_dict() for ref in result.evidence_refs]
        # codex A-1：result.annotated 是带 inline ✅/⚠️/❌ 标记的文本（cite_check._annotate）；
        # 落库它才能让"标红"这一 claim 由产物证明（而非仅末尾追加警告的 validated_output）。
        annotated_with_marks = result.annotated
        validation_summary = {
            "total_segments": 1,
            "valid_citations": len(evidence_refs),
            "fabricated_citations": len(fabricated),
            "fabricated_spans": fabricated[:20],
            # 仅供人读；verify 取 manifest.fabricated_count（源自 fabricated_citations）
            "summary": result.summary,
            "strategy": strategy,
            # 带 ❌ inline 标记的文本（"标红"证据；伪造引用紧跟 ❌）
            "annotated_with_marks": annotated_with_marks,
        }

        # 3) 记录校验完成事件
        await append_event_chained(
            s, run.id, "validation_summary",
            {
                "fabricated_citations": len(fabricated),
                "valid_citations": len(evidence_refs),
                "rejected": rejected,
            },
        )

        # 4) 终态写回 run（save_state 把 evidence_refs / validation_summary 落库；
        #    build_runlog 从此处取 manifest.fabricated_count 与 evidence）
        final_status = "error" if rejected else "done"
        final_output = (
            f"[REJECTED] {reject_msg}" if rejected else result.validated_output
        )
        state = LoopState(
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": final_output},
            ],
            round_idx=1,
            rounds_log=[{"model": "offline-preset (FakeLLM, 无真实调用)"}],
            status=final_status,
            final_output=final_output,
            evidence_refs=evidence_refs,
            validation_summary=validation_summary,
        )
        await append_event_chained(s, run.id, "done", {"status": final_status})
        await save_state(s, run.id, state)

        # 5) 聚合真实 runlog（content_sha256 由 build_runlog 对全文重算）
        runlog = await build_runlog(s, run.id)

        check_info = {
            "summary": result.summary,
            "fabricated": fabricated,
            "evidence_count": len(evidence_refs),
            "rejected": rejected,
            "reject_msg": reject_msg,
            "final_status": final_status,
            "annotated_with_marks": annotated_with_marks,
        }
        return runlog, check_info


# ===========================================================================
# 调用真实 verify_runlog CLI（对外演示的同一命令）
# ===========================================================================
def _run_verify(
    runlog_path: Path,
    corpus_hashes_path: Path,
    *,
    max_fabricated: int,
) -> tuple[int, str]:
    """以子进程跑 scripts/verify_runlog.py，必带 --corpus-hashes（§10.3）。返回 (exit_code, stdout)。"""
    cmd = [
        sys.executable,
        str(_SCRIPT_DIR / "verify_runlog.py"),
        str(runlog_path),
        "--corpus-hashes",
        str(corpus_hashes_path),
        "--max-fabricated",
        str(max_fabricated),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


# ===========================================================================
# 输出分镜
# ===========================================================================
_BAR = "=" * 72


def _print_header(title: str) -> None:
    print("\n" + _BAR)
    print(title)
    print(_BAR)


def _print_verify(label: str, exit_code: int, expected_pass: bool, out: str) -> bool:
    """打印一次 verify 结果，校验是否符合预期。返回是否符合预期。"""
    verdict = "PASS" if exit_code == 0 else "FAIL"
    expect = "PASS" if expected_pass else "FAIL"
    ok = (exit_code == 0) == expected_pass
    flag = "✓ 符合预期" if ok else "✗ 不符合预期！"
    print(f"\n  [{label}] verify → {verdict}（预期 {expect}）{flag}")
    # 缩进打印 CLI 输出（便于录屏看清）
    for line in out.rstrip().splitlines():
        print(f"    | {line}")
    return ok


def _assert_safe_test_db(url: str) -> None:
    """drop_all 风险最高（codex E-3）：跑前断言库名/URL 像测试库，且不等于开发库，
    防 TEST_DATABASE_URL 配错把 ORM 表删到开发库。不满足直接拒绝运行。"""
    if "test" not in url.lower():
        raise SystemExit(
            f"拒绝运行：TEST_DATABASE_URL={url!r} 不含 'test'，"
            "本脚本会 create_all/drop_all，必须指向独立测试库。"
        )
    if url == settings.database_url:
        raise SystemExit(
            "拒绝运行：TEST_DATABASE_URL 与开发库 DATABASE_URL 相同；drop_all 会删开发库表。"
        )


async def _amain(out_dir: Path, keep: bool) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 写语料哈希文件（verify 的 --corpus-hashes 输入）
    corpus_hashes_path = out_dir / "corpus_hashes.json"
    corpus_hashes_path.write_text(
        json.dumps({"hashes": CORPUS_HASHES}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # drop_all 安全断言：必须指向独立测试库，绝不误删开发库（codex E-3）
    _assert_safe_test_db(settings.test_database_url)

    # 建测试库表（跑完 drop_all 清理，不污染开发库）
    engine = create_async_engine(settings.test_database_url, pool_pre_ping=True)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    all_ok = True
    try:
        # ---------------------------------------------------------------
        # 分镜 1 · 正常路径（全部命中语料，零伪造）
        # ---------------------------------------------------------------
        _print_header("分镜 1 · 正常路径（ANNOTATE 策略）：引用全部命中语料")
        rl_clean, info_clean = await _build_one_runlog(
            factory,
            review_text=REVIEW_CLEAN,
            strategy=CitationFailStrategy.ANNOTATE,
            prompt="综述：可信数据 Agent 的语料生产（正常版）",
            label="clean",
        )
        clean_path = out_dir / "runlog_clean.json"
        clean_path.write_text(
            json.dumps(rl_clean, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            f"  引用三色判定: green={info_clean['summary']['green']} "
            f"yellow={info_clean['summary']['yellow']} "
            f"red={info_clean['summary']['red']}"
        )
        print(f"  命中语料证据(evidence_refs): {info_clean['evidence_count']} 条（可溯源到源文档 sha256）")
        print(f"  伪造引用(fabricated): {len(info_clean['fabricated'])} 条")
        print(f"  最终状态: {info_clean['final_status']}（继续输出整份）")
        print(f"  runlog 已写: {clean_path}")
        rc, out = _run_verify(clean_path, corpus_hashes_path, max_fabricated=0)
        all_ok &= _print_verify("正常 / --max-fabricated 0", rc, expected_pass=True, out=out)

        # ---------------------------------------------------------------
        # 分镜 2 · 检出路径（默认 ANNOTATE：标红 + 计入日志，继续输出）
        # ---------------------------------------------------------------
        _print_header("分镜 2 · 检出路径（ANNOTATE 策略，亦为底层默认值）：注入 1 条伪造引用 → 标红 + 计入日志（不拒绝整份）")
        rl_annot, info_annot = await _build_one_runlog(
            factory,
            review_text=REVIEW_INJECTED,
            strategy=CitationFailStrategy.ANNOTATE,
            prompt="综述：可信数据 Agent 的语料生产（注入伪造版，ANNOTATE）",
            label="injected_annotate",
        )
        annot_path = out_dir / "runlog_injected_annotate.json"
        annot_path.write_text(
            json.dumps(rl_annot, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            f"  引用三色判定: green={info_annot['summary']['green']} "
            f"yellow={info_annot['summary']['yellow']} "
            f"red={info_annot['summary']['red']}（红 = 确定性代码反查 records 未命中）"
        )
        print(f"  注入的伪造引用(fabricated): {info_annot['fabricated']}")
        # "标红"证据：annotated 文本里伪造引用紧跟 ❌ 标记（落库于 validation_summary.annotated_with_marks）
        _fake_line = next(
            (ln.strip() for ln in info_annot["annotated_with_marks"].splitlines()
             if _FAKE_DOI in ln),
            "",
        )
        print(f"  标红证据(inline ❌): {_fake_line}")
        print(f"  最终状态: {info_annot['final_status']}（口径：检出标红 + 计入日志，继续输出整份，**不是拒绝**）")
        print(f"  runlog 已写: {annot_path}")
        # 同一份 runlog，两个阈值两种结果：比"零伪造率下降"更可信
        rc0, out0 = _run_verify(annot_path, corpus_hashes_path, max_fabricated=0)
        all_ok &= _print_verify("检出 / --max-fabricated 0", rc0, expected_pass=False, out=out0)
        rc1, out1 = _run_verify(annot_path, corpus_hashes_path, max_fabricated=1)
        all_ok &= _print_verify("检出 / --max-fabricated 1（阈值可配）", rc1, expected_pass=True, out=out1)

        # ---------------------------------------------------------------
        # 分镜 3 · 阻断路径（显式 REJECT：整份拒绝，区别于 ANNOTATE）
        # ---------------------------------------------------------------
        _print_header("分镜 3 · 阻断路径（显式 CitationFailStrategy.REJECT）：同样注入伪造 → 整份拒绝")
        rl_reject, info_reject = await _build_one_runlog(
            factory,
            review_text=REVIEW_INJECTED,
            strategy=CitationFailStrategy.REJECT,
            prompt="综述：可信数据 Agent 的语料生产（注入伪造版，REJECT）",
            label="injected_reject",
        )
        reject_path = out_dir / "runlog_injected_reject.json"
        reject_path.write_text(
            json.dumps(rl_reject, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"  策略: REJECT（显式声明，非默认）")
        print(f"  是否整份拒绝: {info_reject['rejected']}  最终状态: {info_reject['final_status']}")
        print(f"  拒绝原因: {info_reject['reject_msg']}")
        print(f"  口径区别: 这条才是'阻断整份'；分镜 2 的默认 ANNOTATE 是'标红+计入日志+继续输出'。")
        print(f"  runlog 已写: {reject_path}")
        # REJECT 路径产出的 runlog 同样含 fabricated_count=1 → --max-fabricated 0 也 FAIL
        rcr, outr = _run_verify(reject_path, corpus_hashes_path, max_fabricated=0)
        all_ok &= _print_verify("阻断 / --max-fabricated 0", rcr, expected_pass=False, out=outr)

        # ---------------------------------------------------------------
        # 收尾：诚实口径声明
        # ---------------------------------------------------------------
        _print_header("口径声明（现场/录屏务必同步，§10.3）")
        print(
            "  1. 底层默认策略即 ANNOTATE（safety/citation.py 的 strategy 默认值）=\n"
            "     '检出标红 + 计入日志，继续输出整份'，不是拒绝整份；要'阻断整份'必须\n"
            "     显式 CitationFailStrategy.REJECT（分镜 3）。本 demo 三段均显式传策略，演示该行为。\n"
            "  2. '标红'有产物证据：annotated 文本里伪造引用紧跟 inline ❌（落库于\n"
            "     validation_summary.annotated_with_marks），不只是末尾追加一行警告。\n"
            "  3. citation check 是确定性代码反查语料 records（app/cite_check.py），不是 LLM 自评分。\n"
            "  4. verify 全程带 --corpus-hashes，才会启用 evidence_traceable 溯源校验。\n"
            "  5. 不说'哈希链防篡改'：离线重算只证明日志内部自洽；真正防篡改需把 chain_head\n"
            "     外部锚定（不可变存储 / 数字签名 / 时间戳服务），不在本脚本范围。\n"
            "  6. 本脚本只验'引用存在性 + 溯源哈希 + 日志自洽'，不验 final_output 的语义/逻辑正确性。\n"
            "  7. 全程离线：综述/语料/markdown 全部为预置 fixture（模拟缓存命中），不依赖现场真实\n"
            "     OCR/LLM；故它演示的是'机制能检出注入引用 + runlog/verify 链路可跑'，**不**代表\n"
            "     生产语料的独立真实性验证，也不证明外部文献真实存在。"
        )

        _print_header("三段总判")
        print(f"  正常路径   : verify(--max-fabricated 0) → PASS")
        print(f"  检出路径   : verify(0) → FAIL ; verify(1) → PASS（阈值可配）")
        print(f"  阻断路径   : 整份 REJECT + verify(0) → FAIL")
        print(f"\n  全部分镜符合预期: {'是 ✓' if all_ok else '否 ✗'}")
        print(f"  产物目录: {out_dir}")
    finally:
        if not keep:
            async with engine.begin() as c:
                await c.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="零伪造闭环 demo（离线、口径收窄；P0-2）",
    )
    parser.add_argument(
        "--out-dir",
        default=str(_SERVICE_DIR / "demo_artifacts" / "zero_fabrication"),
        help="runlog / corpus_hashes 产物目录（默认 services/agent/demo_artifacts/zero_fabrication）",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="跑完不 drop_all 测试库表（便于复查；默认清理）",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(Path(args.out_dir), args.keep))


if __name__ == "__main__":
    raise SystemExit(main())
