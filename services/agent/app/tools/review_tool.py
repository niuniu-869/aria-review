"""ReviewTool — 安全带强制的文献综述生成工具（BaseTool 子类，P3-2）。

它让 agent 能在一次 run 内产出"可信文献综述"：
  - 引用经 GuardedStream 校验（安全带在工具内部，agent 无法绕过——本工具不接受
    任何"跳过校验"参数）；
  - 伪造引用计入 validation_summary（fabricated_citations / fabricated_spans），
    并随 state 落库，形成可验证日志；
  - 命中语料的真引用产出 EvidenceRef，每条绑文档内容哈希 source_content_sha256。

执行链（复用既有 map+reduce 编排，勿重造）：
  load_project_corpus(project_id) → (paper_markdowns, records[含 content_sha256])
    → run_review(...)（内部 summarize_papers map + generate_review reduce，
       reduce 经 GuardedStream 逐段校验引用）
    → 块级事件 emit + 回写 live LoopState（evidence_refs / validation_summary）。

事件（块级，经 await emit(...)，非逐 token）：
  review_progress    — map+reduce 已完成（含统计：论文数 / 字数 / 引用数）
  validation_summary — 引用校验汇总（含 fabricated_spans）
  evidence_refs      — EvidenceRef 列表（命中语料、带 source_content_sha256）
  review_complete    — 综述全文（一条完整 review_md，非逐 token chunk）

依赖注入：session_factory 构造时传入（兜底）；execute 时优先用 tool_context 中的
session_factory（以与 emit / state 同源）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..harness.tools import BaseTool, ToolResult
from ..review.load import load_project_corpus
from ..review.orchestrate import run_review
from ..review.templates import get_template

logger = logging.getLogger("agent.tools.review")

# 时延控制硬约束（codex P1）：map 逐篇精读每篇一次 LLM 调用 + 单遍 reduce，
# 大语料(89 篇)整体超时。这两个值为常量、不从 LLM 参数读取，防隐藏参数绕过上限。
_MAX_REVIEW_PAPERS = 12  # 超过则精读前 N 篇并在结果中如实标注覆盖范围
_MAP_CONCURRENCY = 6
# topic 低于此长度视为缺失/占位（如 "x"、"无"），从语料标题派生（F-11）
_MIN_TOPIC_CHARS = 4


def _derive_topic_from_corpus(paper_markdowns: list[dict]) -> str:
    """从语料论文标题派生综述主题（F-11：topic 缺失/过短时的语料内派生）。

    取前 3 篇论文标题拼接——主题必须来自语料内容本身，绝不回退项目名
    （项目名仅为标识，可能与研究无关甚至含注入内容）。
    """
    titles: list[str] = []
    for pm in paper_markdowns[:3]:
        meta = pm.get("meta") or {}
        title = str(meta.get("title") or pm.get("title") or "").strip()
        if title:
            titles.append(title)
    return "；".join(titles)


class ReviewTool(BaseTool):
    """文献综述生成工具（安全带强制；产出综述 + 校验日志 + 证据，并回写 state）。

    单轮单次约束（codex P2-7，文档约定）：
      本工具 execute 会回写共享的 ctx.tool_context["state"]（evidence_refs /
      validation_summary）。若同一轮内被并发调用多次，多个实例会互相覆盖该共享 state，
      产生不一致快照。因此约定：每轮（step_once）至多调用本工具一次。本工具不引入确认
      gate（非写工具），仅以此约束规避并发共享 state 互覆盖；如需同轮多综述，应分轮执行。
    """

    tool_id = "review"
    tool_name = "Review Tool"
    description = (
        "对当前项目的入选（included）论文执行 map+reduce 文献综述生成；"
        "引用经安全带逐段校验，伪造引用计入校验日志，命中引用绑文档内容溯源哈希"
    )
    actions = ["generate"]
    # 注意：不含 "write"。本工具不写 DB 业务行、不需确认 gate；它产出综述 + 写
    # 运行期 state，属生成动作。
    tags = ["read", "generate"]

    action_schemas = {
        "generate": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "综述研究主题（应概括本项目语料内容；缺省或过短时自动从已纳入"
                        "文献标题派生，绝不使用项目名）"
                    ),
                },
                "paper_type": {
                    "type": "string",
                    "enum": ["undergrad", "master", "phd", "grant", "proposal", "sci_intro"],
                    "description": (
                        "论型模板（可选）：undergrad=本科毕业论文综述, master=硕士论文综述, "
                        "phd=博士论文综述, grant=国家基金本子综述, proposal=博士开题报告综述, "
                        "sci_intro=SCI论文Introduction。给定时按论型章节大纲分章输出，"
                        "不给定时按通用综述结构输出（向后兼容）。"
                        "【重要】若用户指令中出现 `(paper_type: xxx)` 标记或明确指定论型"
                        "（本科/硕士/博士/基金/开题/SCI），必须在调用本工具时传入对应 paper_type 值"
                        "（undergrad/master/phd/grant/proposal/sci_intro），不得遗漏。"
                    ),
                },
            },
            "required": ["topic"],
        },
    }

    def __init__(self, session_factory: Callable) -> None:
        """
        Args:
            session_factory: 异步会话工厂（兜底）。execute 时优先用 tool_context
                             中的 session_factory（与 emit / state 同源）。
        """
        self._sf = session_factory

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def _execute(
        self, action: str, params: dict[str, Any], context: Any = None,
    ) -> ToolResult:
        if action != "generate":
            return self._fail(action, f"不支持的 action: {action}")

        ctx = context if isinstance(context, dict) else {}
        emit = ctx.get("emit")
        session_factory = ctx.get("session_factory") or self._sf
        override = ctx.get("override")
        project_id = ctx.get("project_id")
        state = ctx.get("state")  # step_once 注入的 live LoopState（回写通道）

        if project_id is None:
            return self._fail(action, "缺少 project_id（无法定位综述语料）")
        if session_factory is None:
            return self._fail(action, "缺少 session_factory（无法访问数据库）")

        topic = (params.get("topic") or "").strip()
        # 常量硬约束，不从 params 读取（schema 已不暴露）：防隐藏参数绕过时延上限 (codex P1)
        concurrency = _MAP_CONCURRENCY
        max_papers = _MAX_REVIEW_PAPERS
        # 论型模板（可选）：paper_type 给定时单遍注入章节大纲 + 抗幻觉指令
        pt = params.get("paper_type")
        template = get_template(pt)

        # 1) 加载项目语料（records 带 content_sha256；codex P1-4：跳过无 markdown/sha256 论文）
        async with session_factory() as s:
            paper_markdowns, records, skipped = await load_project_corpus(s, project_id)

        if not paper_markdowns:
            reason = ""
            if skipped:
                reason = f"（{len(skipped)} 篇 included 论文因无可读全文/无溯源哈希被跳过）"
            return self._fail(
                action, f"项目 {project_id} 无可用 included 论文（语料为空）{reason}",
            )

        # F-11：topic 缺失/过短（疑似占位）时从语料标题派生，绝不回退项目名
        # （项目名仅为标识，可能与研究主题无关甚至含注入内容）。零语料已在上面失败。
        if len(topic) < _MIN_TOPIC_CHARS:
            derived = _derive_topic_from_corpus(paper_markdowns)
            if derived:
                logger.info("[ReviewTool] topic 缺失/过短，已从语料标题派生: %r", derived)
                topic = derived
        if not topic:
            return self._fail(action, "缺少 topic（综述主题）且无法从语料标题派生")

        if skipped:
            logger.info(
                "[ReviewTool] 跳过 %d 篇无可读全文/无溯源哈希的 included 论文: %s",
                len(skipped), [sk.get("paper_id") for sk in skipped],
            )

        # 大语料时延控制：map 逐篇精读每篇一次 LLM 调用，89 篇会远超交互式 run 的合理时延。
        # 取前 max_papers 篇（paper_markdowns/records 同序切片，保 [n] 引用对齐），并在结果
        # 中如实标注覆盖范围（不做静默截断）。
        total_included = len(paper_markdowns)
        capped = total_included > max_papers
        if capped:
            paper_markdowns = paper_markdowns[:max_papers]
            records = records[:max_papers]
            logger.info(
                "[ReviewTool] 语料 %d 篇 > 上限 %d，本次综述精读前 %d 篇（时延控制）",
                total_included, max_papers, max_papers,
            )

        # 2) map + reduce（run_review 内部经 GuardedStream，安全带不可绕过）
        #    template 给定时单遍注入论型章节大纲（codex P1，非逐章 reduce）
        result = await run_review(
            topic=topic,
            paper_markdowns=paper_markdowns,
            records=records,
            template=template,
            concurrency=concurrency,
            override=override,
        )

        # codex P0-2：reduce 阶段校验链失败（如 GuardedStream fail-closed 的
        # ValidationUnavailableError）→ run_review 回填 error；此时 fail-closed：
        # 不回写 state、不发任何块事件（尤其不发 review_complete）、ToolResult 失败。
        # 绝不能让未经校验的文本作为"可信综述"放行。
        reduce_error = result.get("error")
        if reduce_error:
            return self._fail(
                action,
                f"综述生成失败（引用校验链不可用，fail-closed）：{reduce_error}",
            )

        review_md: str = result.get("review_md", "") or ""
        validation_summary: dict = result.get("validation_summary", {}) or {}
        evidence_refs_raw = result.get("evidence_refs", []) or []
        stats: dict = result.get("stats", {}) or {}
        # B4b/B4c：occurrence anchor → 溯源条目映射（前端点击引用跳回原文 block/page）。
        provenance_map: dict = result.get("provenance_map") or {}

        # EvidenceRef → JSON-able dict（每条带 source_content_sha256）
        evidence_refs_dicts = [
            e.to_dict() if hasattr(e, "to_dict") else e for e in evidence_refs_raw
        ]

        # 3) 回写 live LoopState（state 单一真源；save_state 据此落 agent_run 列）。
        #    codex P1-5：原子回写——两个属性同源同次写入，避免半套；任一异常都不留半套。
        if state is not None:
            try:
                state.evidence_refs = evidence_refs_dicts
                state.validation_summary = validation_summary
                # B4b/B4c：溯源映射与上面两个属性同源同次原子写入。
                state.provenance_map = provenance_map
            except Exception as exc:  # noqa: BLE001
                # 回滚到一致空态，绝不留"只写了一半"的不一致快照
                logger.warning("[ReviewTool] 回写 state 失败（回滚避免半套）: %s", exc)
                try:
                    state.evidence_refs = []
                    state.validation_summary = {}
                    state.provenance_map = {}
                except Exception:  # noqa: BLE001
                    pass

        # 4) 块级事件（经 emit；review_complete 是一条完整全文，非逐 token）。
        #    codex P1-5：emit 只是通知通道。校验已通过且 state 已回写后，emit 失败不得
        #    让"已成功校验的 review"被判失败——逐条 try/except 记日志，继续发后续事件。
        emit_errors = 0
        if emit is not None:
            async def _safe_emit(event: dict) -> None:
                nonlocal emit_errors
                try:
                    await emit(event)
                except Exception as exc:  # noqa: BLE001
                    emit_errors += 1
                    logger.warning(
                        "[ReviewTool] emit 事件失败（type=%s，不影响已校验结果）: %s",
                        event.get("type"), exc,
                    )

            await _safe_emit({
                "type": "review_progress",
                "stage": "map_reduce_done",
                "total_papers": stats.get("total_papers", len(paper_markdowns)),
                "review_chars": stats.get("review_chars", len(review_md)),
                "valid_citations": stats.get("valid_citations", len(evidence_refs_dicts)),
                # codex P1-4：报告被跳过的无可读全文/无溯源哈希论文数
                "skipped_papers": len(skipped),
            })
            await _safe_emit({
                "type": "validation_summary",
                **validation_summary,
            })
            await _safe_emit({
                "type": "evidence_refs",
                "evidence_refs": evidence_refs_dicts,
            })
            await _safe_emit({
                "type": "review_complete",
                "review_md": review_md,
                # B4b/B4c：随 review_complete 一并下发，前端可即时建立 anchor→溯源映射，
                # 并随 AgentEvent/RunLog 持久化。
                "provenance_map": provenance_map,
            })

        fabricated_count = validation_summary.get(
            "fabricated_citations", len(validation_summary.get("fabricated_spans", [])),
        )
        # F-13：字数取 stats.review_chars（orchestrate 已剔除 [[anchor:]] 包裹标记，
        # 与 validation_summary.review_chars 同源，可审计）。
        review_chars = stats.get("review_chars", len(review_md))
        summary = (
            f"综述正文 {review_chars} 字符，有效引用 {len(evidence_refs_dicts)} 条，"
            f"伪造引用 {fabricated_count} 条（已计入校验日志），"
            f"溯源锚点 {len(provenance_map)} 个"
            + (
                f"，本次精读前 {len(paper_markdowns)} 篇（共 {total_included} 篇已纳入，为控制时延截断）"
                if capped else ""
            )
            + (f"，跳过 {len(skipped)} 篇无可读全文论文" if skipped else "")
            # codex P1-5：如实反映 emit 部分失败（校验/回写已成功，仅通知通道异常）
            + (f"，{emit_errors} 条事件通知发送失败（不影响已校验结果）" if emit_errors else "")
        )

        return self._ok(
            action,
            data=[{
                "review_chars": review_chars,
                "valid_citations": len(evidence_refs_dicts),
                "fabricated_citations": fabricated_count,
                "total_papers": stats.get("total_papers", len(paper_markdowns)),
            }],
            source="review",
            summary=summary,
        )
