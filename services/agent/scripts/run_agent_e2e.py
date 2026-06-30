#!/usr/bin/env python
"""完整综述 agent e2e 端到端运行脚本。

证明整条可信链路：**用户一句话 → agent 调工具 → 产出可信综述 + 可验证 RunLog**。
与 scripts/run_slr_e2e.py 的区别：综述产出经一次 **agent run（RunController + ReviewTool）**
驱动，而非脚本直接调 run_review；结束时聚合 build_runlog 落盘 + verify_runlog 校验。

用法示例：
  # 最简：使用脚本内置样例，输出 RunLog 到 /tmp
  services/agent/.venv/bin/python scripts/run_agent_e2e.py

  # 指定多格式文档包 + RunLog 输出
  services/agent/.venv/bin/python scripts/run_agent_e2e.py \\
      --offline-fixtures none \\
      --zip /data/docs.zip \\
      --max-papers 6 \\
      --out /tmp/agent_runlog.json

  # 完整参数
  services/agent/.venv/bin/python scripts/run_agent_e2e.py \\
      --zip /data/docs.zip \\
      --max-papers 6 \\
      --topic "分析师跟踪/盈余预测的影响因素与经济后果（中国资本市场）" \\
      --prompt "请为本项目入选论文生成一份文献综述。" \\
      --out /tmp/agent_runlog.json \\
      --language en \\
      --concurrency 4

流程：
  1. 解压 ZIP（**多格式：接受 .pdf/.docx/.pptx/.html**，§0.6 多格式同管线）
  2. 按 --max-papers 截取（保护 MinerU 日配额）
  3. 批量 ingest_pdfs（断点续跑：sha256.md 已存在则跳过 MinerU；多格式经同一管线）
  4. 每次新建唯一命名 Project + included（name 带 uuid4 短后缀，避免撞唯一约束）
  5. 经 RunController 起一个 agent run（user_prompt=一句话），驱动到 done（auto_confirm）
  6. build_runlog 落盘 --out；verify_runlog 打印各 check + ok；打印 stats
  7. 退出码 0（成功 + verify ok）/ 1（任一环节失败）

离线 / 真实双路径（关键设计）：
  - **真实**：有 LLM key（LLMRouter.from_config().has_any_key()==True）→ 用真实 router，
    agent 的 LLM **自主决定**调 review 工具；ingest 真跑 MinerU（若 MinerU key 配齐）或命
    中已缓存 markdown。综述与校验经真实链路。
  - **离线**：无 LLM key → 注入 stub LLM（canned：第 1 轮 review__generate，第 2 轮 final
    answer），驱动 run 到 done；review 链经 FakeLLM 产含 [n] 引用的占位综述。脚本仍能产出
    可验证 RunLog（以「能产出并校验 RunLog」为准）。ingest 需已缓存 markdown（无 MinerU key
    时不真跑 MinerU）。
  - **离线自包含（--offline-fixtures builtin）**：无 MinerU 且 fresh 容器（无任何缓存）也能跑。
    把脚本内置的极小 markdown 语料 seed 进 {CORPORA}/fulltext/<sha256>.md 缓存，ingest
    走缓存命中建 Paper/Attachment，跳过 zip 与 MinerU；再叠加上面的离线 stub LLM，完整
    产出可验证 RunLog。供 `docker compose run --rm demo` 在零 key、fresh 容器内复现可信综述。

注意：本脚本写好供手动触发，真实 MinerU / 真实 LLM 调用由用户控制；--max-papers 默认较小
以保护配额。默认走内置样例；如需真实语料，请传 `--offline-fixtures none --zip <docs.zip>`。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# 把 services/agent 加入 sys.path（脚本直接运行时需要）
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_SERVICE_DIR = _SCRIPT_DIR.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent_e2e")


# ---------------------------------------------------------------------------
# 默认常量
# ---------------------------------------------------------------------------
DEFAULT_ZIP = ""
DEFAULT_MAX_PAPERS = 6
DEFAULT_TOPIC = "分析师跟踪/盈余预测的影响因素与经济后果（中国资本市场）"
DEFAULT_PROMPT = "请为本项目入选论文生成一份文献综述。"
DEFAULT_OUT = "/tmp/agent_runlog.json"
DEFAULT_WORK_DIR = "/tmp/agent_e2e_workdir"
# 多格式：MinerU 2.5+ 支持的扩展名（§0.6 多格式同管线）。
SUPPORTED_EXTS = {".pdf", ".docx", ".pptx", ".html", ".htm"}
BUILTIN_FIXTURE_SENTINELS = {"builtin", "__builtin__"}
BUILTIN_OFFLINE_MARKDOWNS = {
    "01_analyst_coverage.md": """# Analyst Coverage and Forecast Accuracy

Analyst coverage improves information production around listed firms. The study reports that more active analyst following is associated with narrower forecast dispersion and faster incorporation of firm-specific news.

## Evidence

The strongest result is observed in firms with weak prior disclosure, where analyst reports add incremental public information.
""",
    "02_institutional_ownership.md": """# Institutional Ownership and Market Response

Institutional investors shape market reactions by monitoring managers and demanding clearer disclosure. The evidence indicates that higher institutional ownership moderates abnormal returns around annual report releases.

## Evidence

The monitoring channel is stronger for firms with dispersed retail ownership and lower baseline transparency.
""",
    "03_analyst_economic_consequences.md": """# Economic Consequences of Analyst Forecasts

Analyst forecasts influence financing costs, liquidity, and managerial disclosure behavior. Forecast revisions provide a public signal that helps investors update beliefs about firm fundamentals.

## Evidence

The paper links forecast revisions to subsequent changes in trading volume and bid-ask spreads.
""",
}


# ---------------------------------------------------------------------------
# 命令行参数
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_agent_e2e.py",
        description="完整综述 agent e2e 端到端脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--zip", default=DEFAULT_ZIP,
        help="多格式文档包 zip 路径；传 --offline-fixtures none 时使用",
    )
    parser.add_argument(
        "--offline-fixtures", default="builtin", metavar="DIR",
        help=(
            "离线自包含 demo 模式：传 builtin 使用脚本内置极小 markdown 语料；也可给定"
            "一个本地 markdown 目录。脚本会把这些 markdown seed 进 {CORPORA}/fulltext/"
            "<sha256>.md 缓存并入库 Paper/Attachment（跳过 zip 解压与 MinerU），无任何"
            "外部 key 也能端到端产出可验证 RunLog。传 none 可改走 --zip。"
        ),
    )
    parser.add_argument(
        "--max-papers", type=int, default=DEFAULT_MAX_PAPERS,
        help=f"最多摄取篇数，保护 MinerU 日配额（默认: {DEFAULT_MAX_PAPERS}）",
    )
    parser.add_argument(
        "--topic", default=DEFAULT_TOPIC,
        help=f"综述研究主题（默认: {DEFAULT_TOPIC!r}）",
    )
    parser.add_argument(
        "--prompt", default=DEFAULT_PROMPT,
        help=f"给 agent 的一句话指令（默认: {DEFAULT_PROMPT!r}）",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT,
        help=f"RunLog JSON 输出路径（默认: {DEFAULT_OUT}）",
    )
    parser.add_argument(
        "--out-corpus-hashes", default=None, metavar="PATH",
        help=(
            "可选：把真实语料 content_sha256 集合写到该 JSON（{\"hashes\":[...]}），"
            "供独立 `verify_runlog.py --corpus-hashes` 做引用溯源校验。"
        ),
    )
    parser.add_argument(
        "--auto-confirm", action="store_true", default=True,
        help="自动确认 agent 写动作（默认开启；本脚本一贯 auto_confirm 驱动到 done）。",
    )
    parser.add_argument(
        "--work-dir", default=DEFAULT_WORK_DIR,
        help=f"文档解压工作目录（默认: {DEFAULT_WORK_DIR}）",
    )
    parser.add_argument(
        "--language", default="en", choices=["en", "ch"],
        help="MinerU 语言参数（默认: en）",
    )
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help="review 工具 map 阶段 LLM 并发数（默认: 4）",
    )
    parser.add_argument(
        "--max-fabricated", type=int, default=0,
        help="verify_runlog 允许的最大伪造引用数（默认: 0 = 零容忍）",
    )
    parser.add_argument(
        "--run-timeout", type=float, default=600.0,
        help="等待 agent run 到达终态的超时秒数（默认: 600）",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def extract_zip_to_workdir(zip_path: Path, work_dir: Path) -> list[Path]:
    """解压 ZIP 中的**多格式**文档到工作目录（展平），返回所有受支持文档路径。

    与 run_slr_e2e 仅取 .pdf 不同：这里接受 SUPPORTED_EXTS（pdf/docx/pptx/html），
    体现「多格式经同一 ingest 管线」。
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    doc_paths: list[Path] = []

    logger.info("[解压] %s → %s", zip_path, work_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            ext = Path(name).suffix.lower()
            if ext not in SUPPORTED_EXTS:
                continue
            filename = Path(name).name
            out_path = work_dir / filename
            if not out_path.exists():
                zf.extract(name, work_dir)
                extracted = work_dir / name
                if extracted != out_path and extracted.exists():
                    extracted.rename(out_path)
            doc_paths.append(out_path)

    # 统计各格式数量，便于确认多格式覆盖
    by_ext: dict[str, int] = {}
    for p in doc_paths:
        by_ext[p.suffix.lower()] = by_ext.get(p.suffix.lower(), 0) + 1
    logger.info("[解压] 共 %d 个受支持文档，按格式: %s", len(doc_paths), by_ext)
    return doc_paths


def _write_seed_markdowns(named_markdowns: dict[str, str], source_dir: Path) -> list[Path]:
    """把内置 markdown 写入运行期目录，返回可交给 ingest_pdfs 的源文件路径。"""
    source_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for name, markdown in sorted(named_markdowns.items()):
        path = source_dir / name
        path.write_text(markdown, encoding="utf-8")
        out.append(path)
    return out


def _seed_markdown_paths(md_files: list[Path], cache_dir: Path) -> list[Path]:
    """把 markdown 源文件按内容 sha256 写入 fulltext 缓存。"""
    for md in md_files:
        content = md.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        cache_path = cache_dir / f"{sha256}.md"
        if not cache_path.exists():
            cache_path.write_bytes(content)
            logger.info("[fixtures] seed 缓存 %s → %s", md.name, cache_path.name)
        else:
            logger.info("[fixtures] 缓存已存在，跳过 %s（%s）", md.name, cache_path.name)
    logger.info("[fixtures] 共 seed %d 篇样例 markdown 到 %s", len(md_files), cache_dir)
    return md_files


def seed_offline_fixtures(fixtures_dir: Path) -> list[Path]:
    """离线自包含 demo：把内置或本地样例 markdown seed 进语料缓存。

    设计（最大化复用 ingest_pdfs 既有「缓存命中跳过 MinerU」路径）：
      - 把每个样例 `.md` 文件本身当作「原始文件」；
      - 计算其**文件内容 sha256**（与 ingest_pdfs 内部 _sha256_of_file 同口径）；
      - 复制到 {BIBLIOCN_CORPORA_DIR}/fulltext/<sha256>.md（即缓存 markdown 落点）；
      - 返回这些 `.md` 文件路径列表，交给 ingest_pdfs：它会对同一文件再算出**相同**
        sha256，发现 <sha256>.md 已存在 → 走缓存命中分支（读盘 + 建 Paper/Attachment，
        sha256 与 markdown_path 都对得上），全程不触 MinerU。

    如此一来：无 MinerU key、fresh 容器（无任何已缓存 markdown）也能产出含可溯源证据的
    入选语料，content_sha256 = Attachment.sha256 = markdown 文件名 stem，verify_runlog
    的引用溯源校验可对上真集合。

    Args:
        fixtures_dir: `builtin` 或含样例 markdown 的目录（每个 *.md 一篇）。

    Returns:
        所有样例 *.md 文件路径（按文件名排序，保证 demo 可复现）。
    """
    from app.config import settings  # 延迟导入，避免 --help 触发配置

    cache_dir = Path(settings.corpora_dir) / "fulltext"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if fixtures_dir.as_posix() in BUILTIN_FIXTURE_SENTINELS:
        source_dir = Path(settings.corpora_dir) / "offline_demo_sources"
        md_files = _write_seed_markdowns(BUILTIN_OFFLINE_MARKDOWNS, source_dir)
        return _seed_markdown_paths(md_files, cache_dir)

    if not fixtures_dir.exists() or not fixtures_dir.is_dir():
        raise FileNotFoundError(f"offline-fixtures 目录不存在: {fixtures_dir}")

    md_files = sorted(fixtures_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"offline-fixtures 目录无 *.md 文件: {fixtures_dir}")

    return _seed_markdown_paths(md_files, cache_dir)


def _resp(message: dict) -> tuple[dict, str]:
    """构造 call_llm_with_fallback 的 stub 返回（OpenAI 兼容）。"""
    return ({"choices": [{"message": message, "finish_reason": "stop"}]}, "stub-model")


def _make_offline_llm(topic: str, concurrency: int = 4):
    """离线 stub LLM：第 1 轮调 review__generate，第 2 轮纯文本 final answer → done。

    无 LLM key 时用，使脚本在离线环境也能驱动一个 agent run 产出可验证 RunLog。
    P2-c：把 --concurrency 真正透传进 review__generate 工具参数（ReviewTool 的
    action_schema 接受 concurrency 并传给 run_review 的 map 阶段），离线路径不再丢弃该参数。
    """
    review_args = json.dumps(
        {"topic": topic, "concurrency": concurrency}, ensure_ascii=False,
    )
    call_count = {"n": 0}

    async def fake_llm(router, model_names, messages, tools=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _resp({
                "role": "assistant",
                "content": "我将为本项目入选论文生成文献综述。",
                "tool_calls": [{
                    "id": "rev-1", "type": "function",
                    "function": {"name": "review__generate", "arguments": review_args},
                }],
            })
        return _resp({
            "role": "assistant",
            "content": "综述已生成，引用均经安全带校验，可在 RunLog 中核验。",
        })

    return fake_llm


# ---------------------------------------------------------------------------
# 核心异步流程
# ---------------------------------------------------------------------------

async def run_pipeline(args: argparse.Namespace) -> int:
    """主管线，返回退出码（0=成功且 verify ok，1=失败）。"""
    # 延迟导入：避免 --help 时触发配置/DB 连接。
    from unittest.mock import patch

    from app.agent.context import AgentContext
    from app.agent.prompts import AGENT_SYSTEM, WRAP_UP
    from app.agent.registry_factory import build_registry
    from app.agent.run_controller import RunController
    from app.agent.runlog import build_runlog
    from app.agent.runlog_verify import verify_runlog
    from app.db import SessionLocal
    from app.harness.events import SubscribableEventPublisher
    from app.harness.llm import LLMRouter
    from app.ingest.fulltext import ingest_pdfs
    from app.repositories import agent_run as run_repo
    from app.review.load import load_project_corpus
    from app.repositories.project import (
        add_paper_to_project,
        create_project,
        set_inclusion,
    )

    zip_path = Path(args.zip)
    work_dir = Path(args.work_dir)
    out_path = Path(args.out)
    topic = args.topic
    prompt = args.prompt
    offline_fixtures = (
        None
        if str(args.offline_fixtures).strip().lower() in {"", "none", "off", "false"}
        else Path(args.offline_fixtures)
    )

    t_start = time.monotonic()

    # ------------------------------------------------------------------
    # 1. 取得待摄取文档：离线 fixtures 模式 or zip 解压（多格式）+ 截取
    # ------------------------------------------------------------------
    if offline_fixtures is not None:
        # 离线自包含 demo：seed 样例 markdown 进缓存，后续 ingest 走缓存命中，无需 MinerU。
        try:
            all_docs = seed_offline_fixtures(offline_fixtures)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            print(f"\n[BLOCKED] {exc}", flush=True)
            return 1
        selected = all_docs[: args.max_papers]
        if not selected:
            print("\n[BLOCKED] offline-fixtures 目录无可用样例 markdown", flush=True)
            return 1
        print(
            f"\n[阶段 1/4] 离线 fixtures 模式：选取 {len(selected)}/{len(all_docs)} 篇样例"
            f"（dir={offline_fixtures}, max={args.max_papers}）",
            flush=True,
        )
    else:
        if not zip_path.exists():
            logger.error("ZIP 文件不存在: %s", zip_path)
            print(f"\n[BLOCKED] ZIP 文件不存在: {zip_path}", flush=True)
            return 1

        all_docs = extract_zip_to_workdir(zip_path, work_dir)
        selected = all_docs[: args.max_papers]
        if not selected:
            print("\n[BLOCKED] 解压后无受支持文档（pdf/docx/pptx/html）", flush=True)
            return 1
        print(
            f"\n[阶段 1/4] 选取 {len(selected)}/{len(all_docs)} 个文档（max={args.max_papers}）",
            flush=True,
        )

    # ------------------------------------------------------------------
    # 2. 批量 ingest（多格式同管线；缓存命中跳过 MinerU，便于断点续跑）
    # ------------------------------------------------------------------
    print(f"\n[阶段 2/4] 批量摄取 {len(selected)} 个文档（多格式经同一管线）", flush=True)
    async with SessionLocal() as session:
        ingest_results = await ingest_pdfs(
            paths=selected, language=args.language, session=session,
        )

    valid = [
        r for r in ingest_results
        if r.get("status") in ("done", "cached") and r.get("paper_id") is not None
    ]
    failed = [r for r in ingest_results if r.get("status") == "failed"]
    for r in ingest_results:
        icon = {"done": "✓", "cached": "→", "failed": "✗"}.get(r.get("status"), "?")
        suffix = f" — {r.get('err')}" if r.get("status") == "failed" else ""
        print(f"  {icon} {Path(r.get('pdf_path', '')).name}: {r.get('status')}{suffix}",
              flush=True)
    print(f"  摄取成功 {len(valid)} 篇，失败 {len(failed)} 篇", flush=True)

    if not valid:
        print("\n[BLOCKED] 所有文档摄取均失败（无 MinerU key 时需已缓存 markdown）。",
              flush=True)
        return 1

    # ------------------------------------------------------------------
    # 3. 每次新建唯一命名 Project + included
    # ------------------------------------------------------------------
    # P2-b：name 用 uuid4 短后缀保证每次唯一，避免同 topic 同秒/并发重跑撞
    # uq_project_name 唯一约束（旧实现 int(time.time()) 同秒会崩，且注释误称"幂等"）。
    print(f"\n[阶段 3/4] 建 Project + 关联 {len(valid)} 篇 included", flush=True)
    async with SessionLocal() as session:
        project = await create_project(session, {
            "name": f"AgentE2E: {topic[:40]} {uuid4().hex[:8]}",
            "research_question": topic,
            "description": "P3-3 agent e2e 自动建立（多格式 + 可验证 RunLog）",
        })
        project_id = project.id
        for order, r in enumerate(valid):
            pp = await add_paper_to_project(
                session, project_id=project_id, paper_id=r["paper_id"],
                added_by="agent", order=order,  # project_paper.added_by 列为 String(8)，用规范值
            )
            await set_inclusion(session, pp.id, "included")
    print(f"  Project id={project_id}，关联 {len(valid)} 篇", flush=True)

    # ------------------------------------------------------------------
    # 4. 经 RunController 起一个 agent run（离线/真实双路径）
    # ------------------------------------------------------------------
    router = LLMRouter.from_config()
    has_key = router.has_any_key()
    mode = "真实（agent LLM 自主调工具）" if has_key else "离线（stub LLM canned 调工具）"
    print(f"\n[阶段 4/4] 经 agent run 产出综述 — 模式: {mode}", flush=True)

    publisher = SubscribableEventPublisher()
    default_model = "deepseek-chat"

    # P2-c：真实路径下 agent LLM 自主决定工具参数，无法强制注入；把 concurrency 作为
    # 显式约束写进给 agent 的指令，使该参数在真实路径也真正影响 review 工具调用（map
    # 阶段并发），不再是「声明了却谁也不用」的死参数。离线路径则直接透传进 stub 工具参数。
    effective_prompt = (
        f"{prompt}\n\n（执行约束：调用 review 综述工具时，map 阶段并发数 "
        f"concurrency 请设为 {args.concurrency}。）"
        if has_key else prompt
    )

    async def _build_ctx(pid: int) -> AgentContext:
        registry = build_registry(SessionLocal, None)  # ReviewTool 不需 r_client
        return AgentContext(
            registry=registry,
            llm_router=router,
            model_names=[default_model],
            system_prompt=AGENT_SYSTEM,
            tool_ids=None,
            max_rounds=6,
            wrap_up_prompt=WRAP_UP,
        )

    ctrl = RunController(SessionLocal, publisher, _build_ctx)

    async def _drive_to_terminal() -> tuple[int, str]:
        run_id = await ctrl.create(
            project_id=project_id, user_prompt=effective_prompt, auto_confirm=True,
        )
        ctrl.start(run_id)

        deadline = asyncio.get_event_loop().time() + args.run_timeout
        status = "running"
        while asyncio.get_event_loop().time() < deadline:
            async with SessionLocal() as s:
                run = await run_repo.get_run(s, run_id)
            status = run.status if run else "missing"
            if status in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(0.5)
        return run_id, status

    if has_key:
        # 真实路径：agent 的 LLM 自主决定调 review 工具。
        run_id, status = await _drive_to_terminal()
    else:
        # 离线路径：stub LLM（canned 调工具 + final answer）。P2-c：透传 concurrency。
        with patch(
            "app.harness.engine.call_llm_with_fallback",
            new=_make_offline_llm(topic, args.concurrency),
        ):
            run_id, status = await _drive_to_terminal()

    print(f"  agent run id={run_id} 终态: {status}", flush=True)
    if status != "done":
        print(f"\n[FAIL] agent run 未到达 done（实为 {status}）。", flush=True)
        # 仍尽量产出 RunLog 供排查
        async with SessionLocal() as s:
            try:
                runlog = await build_runlog(s, run_id)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(runlog, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                print(f"  （已落 RunLog 供排查: {out_path}）", flush=True)
            except Exception as exc:  # noqa: BLE001
                logger.error("build_runlog 失败: %s", exc)
        return 1

    # ------------------------------------------------------------------
    # 5. build_runlog 落盘 + verify_runlog
    # ------------------------------------------------------------------
    async with SessionLocal() as s:
        runlog = await build_runlog(s, run_id)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(runlog, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\nRunLog 已写到: {out_path}", flush=True)

    # P1：语料内容哈希集合（evidence_traceable 校验的真集合）必须来自**真实摄取语料**，
    # 而非从 runlog.evidence_refs 自己反推（那样溯源校验变成自证：拿证据的 sha 去验证
    # 证据的 sha，必然命中）。这里用 load_project_corpus 返回的 records 的 content_sha256
    # 集合——即「真实喂给综述的入选论文全文 sha 全集」，溯源校验才是「证据 sha 命中真实
    # 语料 sha」的真校验。
    async with SessionLocal() as s:
        _paper_markdowns, _records, _skipped = await load_project_corpus(s, project_id)
    corpus_hashes = {
        r.get("content_sha256") for r in _records if r.get("content_sha256")
    }
    if not corpus_hashes:
        # 真实语料 sha 集合为空 → 无法对证据做真溯源校验，视为 e2e 失败（不退化为自证）。
        print(
            "\n[FAIL] 真实语料 content_sha256 集合为空，无法对证据做真溯源校验"
            f"（included 论文 {len(_records)} 条 / skipped {len(_skipped)} 条）。",
            flush=True,
        )
        return 1

    # 可选：把真实语料哈希集合写盘，供独立 verify_runlog.py --corpus-hashes 做溯源校验。
    if args.out_corpus_hashes:
        ch_path = Path(args.out_corpus_hashes)
        ch_path.parent.mkdir(parents=True, exist_ok=True)
        ch_path.write_text(
            json.dumps({"hashes": sorted(corpus_hashes)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"语料哈希集合已写到: {ch_path}", flush=True)

    report = verify_runlog(
        runlog,
        corpus_content_hashes=corpus_hashes,  # P1：始终传真集合，绝不传 None 跳过溯源
        max_fabricated=args.max_fabricated,
    )

    # ------------------------------------------------------------------
    # 6. 打印 stats + 校验结果
    # ------------------------------------------------------------------
    manifest = runlog.get("manifest", {})
    t_total = time.monotonic() - t_start
    print(f"\n{'='*60}", flush=True)
    print("【RunLog 摘要】", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  run.status            : {runlog.get('run', {}).get('status')}", flush=True)
    print(f"  event_count           : {manifest.get('event_count')}", flush=True)
    print(f"  tool_invocation_count : {manifest.get('tool_invocation_count')}", flush=True)
    print(f"  evidence_count        : {manifest.get('evidence_count')}", flush=True)
    print(f"  fabricated_count      : {manifest.get('fabricated_count')}", flush=True)
    print(f"  chain_head            : {manifest.get('chain_head', '')[:16]}...", flush=True)
    print(f"  脚本总耗时            : {t_total:.1f}s", flush=True)

    print(f"\n{'='*60}", flush=True)
    print("【verify_runlog 校验结果】", flush=True)
    print(f"{'='*60}", flush=True)
    for name, ok in report.checks.items():
        print(f"  {'✓' if ok else '✗'} {name}: {ok}", flush=True)
    if report.errors:
        print("\n  明细:", flush=True)
        for e in report.errors:
            print(f"    - {e}", flush=True)

    # P1：显式要求综述确实产出了可溯源证据。配合离线 stub 第 2 轮无条件 final answer，
    # 若 ReviewTool 失败/语料为空，run 仍可能是 done——但没有任何可信证据。此时即便
    # run==done、report.ok，也判 e2e 失败（没产出可信综述即不算跑通）。
    evidence_count = int(manifest.get("evidence_count") or 0)
    if evidence_count <= 0:
        print(
            f"\n[FAIL] evidence_count={evidence_count}（综述未产出任何可溯源证据）。"
            "即便 run 终态为 done，也判 e2e 失败：没有可信证据即不算综述跑通。",
            flush=True,
        )
        return 1

    if report.ok:
        print(f"\n[DONE] 综述 e2e 跑通且 RunLog 校验通过 (ok=True): {out_path}", flush=True)
        return 0
    print(f"\n[FAIL] RunLog 校验未通过 (ok=False)。", flush=True)
    return 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    exit_code = asyncio.run(run_pipeline(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
