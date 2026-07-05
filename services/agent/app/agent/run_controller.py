"""RunController — 把 harness 接成「可创建/驱动/持久化」的 agent runtime。

职责：
  - create(): 落库一条 AgentRun（status=running），并把初始 messages
    （system=AGENT_SYSTEM, user=user_prompt）写进 LoopState 并 save_state。
  - start(): 在后台 asyncio.Task 中驱动一个 run（防重复起 task）；记 llm_override 入内存。
  - _drive(): 载入 run → 重建 LoopState → 构建 ctx → 据 auto_confirm 装 confirm_check →
    单步推进直到非 running → 每步 save_state；终态按 status 分支收尾（codex P0：
    awaiting_confirmation/paused 不发终态、流保持打开）。事件经哈希链落库 + 扇出。
  - confirm(): 对 awaiting_confirmation 的 run 放行/拒绝队首写工具，按序消费队列，
    协议完成则后台续跑（engine.apply_confirmation 实现核心逻辑）。

并发与异常约定（取舍说明）：
  - 单个 run 由单一 Task 串行驱动，emit 串行调用 → 哈希链 append 不竞态。
  - _drive 内 try/except 包整个驱动：任何异常都标 status=failed + emit(ERROR) +
    终态 save_state，不向外传播。done_callback 只做 _tasks 清理 + 日志，
    不在 callback 里做 await（asyncio 限制）。这是最稳妥版本，无竞态、无悬挂异常。
  - _tasks 持强引用防 Task 被 GC（codex 阻塞 #11）。
  - _overrides 内存保存 per-run X-LLM-Key override，供 confirm 续跑复用；服务重启丢失。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from ..errors import ApiError
from ..harness.config import get_config
from ..harness.engine import LoopState, apply_confirmation, step_once
from ..harness.events import EventType
from ..harness.llm import OverrideLLMConfig
from ..repositories import agent_run as repo
from ..repositories import project as project_repo
from ..run_status import is_terminal_run_status, normalize_run_status
from .confirm import needs_confirmation
from .context import AgentContext
from .prompts import AGENT_SYSTEM

logger = logging.getLogger("agent.run_controller")

# _drive 的 llm_override 哨兵：区分「未传（从 _overrides 读）」与「显式传 None（清 key）」。
_UNSET = object()

# 多轮对话记忆边界：每次新消息本是一条独立 run，create() 把本项目最近若干轮已完成
# 对话（用户指令 + 最终回复）以 user/assistant 交替形式拼进初始 messages，让 agent 跨消息
# 记得上文。上限用于控住上下文体积——超长回复（如综述全文）按字数截断（对话语境只需
# 「记得做过什么」，全文另存工件），回溯轮数封顶避免上下文无界增长。
_HISTORY_MAX_TURNS = 6            # 最多回溯 6 轮历史对话
_HISTORY_USER_MAX_CHARS = 1000   # 单条历史用户指令上限
_HISTORY_ASSISTANT_MAX_CHARS = 1500  # 单条历史 assistant 回复上限


def _truncate_history(text: str, limit: int) -> str:
    """按字数截断历史消息，超限尾部标注原文长度（保留可读性、控住上下文体积）。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[已截断，原文共 {len(text)} 字]"


class RunController:
    """驱动 agent run 的控制器（创建 / 后台驱动 / 事件落库与扇出）。"""

    def __init__(
        self,
        session_factory: Callable,
        publisher,
        build_ctx: Callable[[int], Awaitable[AgentContext]],
    ) -> None:
        """Args:
            session_factory: async_sessionmaker，每次 DB 操作开独立会话。
            publisher: SubscribableEventPublisher（或满足 EventPublisher 协议者）。
            build_ctx: async (project_id) -> AgentContext，构建运行所需静态上下文。
        """
        self._session_factory = session_factory
        self._publisher = publisher
        self._build_ctx = build_ctx
        # run_id -> Task：强引用防 GC（codex 阻塞 #11）。
        self._tasks: dict[int, asyncio.Task] = {}
        # run_id -> per-request LLM 覆盖（X-LLM-Key）。仅内存：服务重启会丢失，
        # 重启后对 awaiting_confirmation 的 run 续跑将退回平台默认 key（已知取舍）。
        self._overrides: dict[int, OverrideLLMConfig | None] = {}
        self._sciverse_overrides: dict[int, dict | None] = {}
        # 协作式暂停信号（仅内存）：pause() 写入，_drive 在每轮 step 后检查 → 把
        # state.status 置 paused、save_state、退出 while 循环（非终态、不发终态事件）。
        # 用集合而非 DB 轮询，避免每轮多一次读；单进程单任务驱动下无竞态。
        self._pause_requested: set[int] = set()

    def channel(self, run_id: int) -> str:
        """该 run 的事件频道名（与 publish_run_event 约定一致）。"""
        return f"run:{run_id}:events"

    async def create(
        self,
        project_id: int,
        user_prompt: str,
        auto_confirm: bool = False,
    ) -> int:
        """创建并落库一条 AgentRun，写入初始 messages，返回 run_id。

        auto_confirm 原样落库到 AgentRun，M2（P2-2）已接入：_drive 据 run.auto_confirm
        决定是否给 step_once 传 confirm_check —— False 时写工具触发人工确认（run 挂起
        awaiting_confirmation，发 tool_confirm_required，经 confirm() 放行）；True 时写工具
        直接执行（仍走 ToolInvocation 幂等短路）。
        """
        async with self._session_factory() as s:
            run = await repo.create_run(
                s, project_id=project_id, auto_confirm=auto_confirm,
            )
            run_id = run.id

        # 注入"当前项目身份"到 system prompt：缺它时模型不知道自己在哪个项目，
        # 会反复调 project.list 盲找、最终以"未提供当前项目标识"收场，综述工具
        # （基于本项目 included 语料、自动从 tool_context 取 project_id）从不被调用。
        system_prompt = AGENT_SYSTEM + await self._project_block(project_id)

        # 多轮对话记忆：加载本项目最近已完成对话，拼成 user/assistant 交替消息，
        # 让本条新 run 延续跨消息上文（首轮无历史 → 空列表 → 退回 [system, user]）。
        history = await self._history_messages(project_id, exclude_run_id=run_id)

        # 初始 LoopState：system + 历史多轮 + 当前 user 消息。
        # user_prompt 单独存一份：注入历史后 messages[1] 不再是本 run 的 user，
        # 供下一轮 list_recent_dialog 权威取回本 run 的原始指令（codex P1）。
        state = LoopState(
            messages=[
                {"role": "system", "content": system_prompt},
                *history,
                {"role": "user", "content": user_prompt},
            ],
            user_prompt=user_prompt,
        )
        async with self._session_factory() as s:
            await repo.save_state(s, run_id, state)
        return run_id

    async def _history_messages(
        self, project_id: int, exclude_run_id: int,
    ) -> list[dict]:
        """加载本项目最近已完成对话，构建真实多轮 messages（user/assistant 交替）。

        每条用户消息本是一条独立 run（初始只 seed 当前 prompt），跨消息即丢上文。此处把
        最近 N 轮 (用户指令, 最终回复) 以真实对话形式拼进初始 messages，模型即可延续
        「先抛主题、再追加动作」的多轮语境。超长回复按上限截断（对话只需记得做过什么，
        综述全文另存工件）。加载失败退回空历史，绝不阻断建 run。
        """
        try:
            async with self._session_factory() as s:
                turns = await repo.list_recent_dialog(
                    s, project_id,
                    exclude_run_id=exclude_run_id,
                    max_turns=_HISTORY_MAX_TURNS,
                )
        except Exception:  # noqa: BLE001 — 历史加载失败退回单轮，不阻断建 run
            logger.exception(
                "[RunController] 加载多轮历史失败 project_id=%s", project_id,
            )
            return []

        msgs: list[dict] = []
        for user_turn, assistant_turn in turns:
            u = _truncate_history(user_turn, _HISTORY_USER_MAX_CHARS)
            a = _truncate_history(assistant_turn, _HISTORY_ASSISTANT_MAX_CHARS)
            if not u or not a:
                continue
            msgs.append({"role": "user", "content": u})
            msgs.append({"role": "assistant", "content": a})
        return msgs

    async def _project_block(self, project_id: int) -> str:
        """构建"当前项目身份"提示块，拼到 system prompt 后。

        让模型明确"本项目/当前项目/已纳入语料"指哪个项目，并把"写综述"直接导向
        综述工具（自动基于本项目 included 语料）。注入失败不阻断 run，退回空串。
        """
        try:
            async with self._session_factory() as s:
                proj = await project_repo.get_project(s, project_id)
                if proj is None:
                    return ""
                pairs = await project_repo.list_project_papers(s, project_id)
                included = sum(
                    1 for pp, _p in pairs if pp.inclusion_status == "included"
                )
            return (
                f"\n\n【当前工作上下文】你正在为**项目 #{project_id}「{proj.name}」**服务，"
                f"该项目已纳入 {included} 篇文献作为综述语料。\n"
                "- 用户说\"本项目/当前项目/已纳入文献/现有语料\"一律指这个项目；"
                f"**不要调用 project.list 去查找或罗列项目**——你就在项目 #{project_id} 中。\n"
                "- 用户要求\"写综述/生成综述/文献综述\"时，**直接调用「综述」工具**"
                "（它自动基于本项目已纳入语料生成可溯源综述，无需你传 project_id）；"
                "用户指定论型(指令含 paper_type)时调用须带该参数。\n"
                "- 仅当本项目确无可用语料(已纳入为 0)时，才提示用户先检索/纳入文献。"
            )
        except Exception:  # noqa: BLE001 — 注入失败退回纯 persona，不阻断 run
            logger.exception("[RunController] 注入项目上下文失败 project_id=%s", project_id)
            return ""

    def start(
        self,
        run_id: int,
        *,
        llm_override: OverrideLLMConfig | None = None,
        sciverse_override: dict | None = None,
    ) -> None:
        """在后台 Task 中驱动 run；若已有未完成的 Task 则不重复起。

        把 llm_override 存进 self._overrides[run_id]（内存）以便 _drive / 确认续跑复用：
        resume 时不再要求重新带 X-LLM-Key。显式传 None 会覆写为 None（清除旧 key）。
        """
        self._overrides[run_id] = llm_override
        self._sciverse_overrides[run_id] = sciverse_override
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            logger.info("[RunController] run %s already running, skip start", run_id)
            return
        task = asyncio.create_task(self._drive(run_id))
        self._tasks[run_id] = task
        task.add_done_callback(self._on_done)

    def _make_emit(self, run_id: int) -> Callable[[dict], Awaitable[None]]:
        """构建该 run 的事件 emit 闭包：经哈希链落库（防篡改）+ 扇出给 publisher。

        改用 append_event_chained（P2-1）：next_seq + prev_hash + event_hash 一体落库，
        使 agent_event 哈希链真正写入（替代旧的手工 next_seq + append_event）。
        落库后把带 seq 的事件扇出，SSE/订阅者据 seq 去重补发。串行调用，无竞态。
        """
        async def emit(ev: dict) -> None:
            async with self._session_factory() as s:
                stored = await repo.append_event_chained(
                    s, run_id, ev.get("type", ""), ev,
                )
            await self._publisher.publish(
                self.channel(run_id), {**ev, "seq": stored.seq},
            )

        return emit

    async def _build_run_ctx(
        self,
        run,
        emit: Callable[[dict], Awaitable[None]],
    ) -> AgentContext:
        """构建并按 run 注入运行期上下文（_drive / confirm 共用）。

        注入：run_id / session_factory / tool_context（含 run_id/project_id/emit/
        session_factory/override，M3 ReviewTool 据此回写证据/发事件）。保持向后兼容：
        旧 build_ctx 不感知这些字段，由本方法事后赋值。
        """
        ctx = await self._build_ctx(run.project_id)
        ctx.run_id = run.id
        ctx.session_factory = self._session_factory
        ctx.tool_context = {
            "run_id": run.id,
            "project_id": run.project_id,
            "emit": emit,
            "session_factory": self._session_factory,
            "override": self._overrides.get(run.id),
            "sciverse": self._sciverse_overrides.get(run.id),
        }
        return ctx

    async def _drive(self, run_id: int, llm_override=_UNSET) -> None:
        """驱动一个 run 直到终态。整个驱动用 try/except 包裹，异常标 failed。

        终态分支（codex P0 修复）：while 退出后按 state.status 分支收尾，**仅**对
        done/failed/cancelled 发终态事件并清 override；awaiting_confirmation/paused
        循环内已 save_state，直接 return 不发终态（SSE 客户端继续等待 / 展示 ConfirmCard，
        流不关闭）。

        llm_override：保留可选位置参数仅为兼容旧测试 _drive(run_id, None)；非 _UNSET 时
        写入 self._overrides[run_id]。实际驱动恒从 self._overrides 读（spec B）。
        """
        if llm_override is not _UNSET:
            self._overrides[run_id] = llm_override

        emit = self._make_emit(run_id)

        try:
            # 1) 载入 run + 重建完整 LoopState 快照 + 构建 ctx
            async with self._session_factory() as s:
                run = await repo.get_run(s, run_id)
                if run is None:
                    raise ValueError(f"AgentRun {run_id} not found")
                state = await repo.get_state(s, run_id)

            if state is None:
                state = LoopState(messages=[])
            # codex P0 守卫：仅对「可续跑」的状态拉回 running 驱动循环；已终态
            # （done/failed/cancelled）的 run 绝不能被重新驱动——直接 return，避免重复
            # LLM/工具副作用（应对 resume/confirm/start 在终态被误调用，或与 cancel 的
            # 竞态）。终态事件在该 run 首次到达终态时已发过，此处不重复发。
            if is_terminal_run_status(state.status):
                logger.info(
                    "[RunController] run %s already terminal (%s), skip drive",
                    run_id, state.status,
                )
                return
            # 续跑：终态字段已随快照恢复，把可续跑状态（running/paused）拉回 running。
            state.status = "running"
            # 本次驱动开始：清掉可能残留的旧 pause 信号（resume 重新驱动时不应被旧信号误暂停）。
            self._pause_requested.discard(run_id)

            ctx = await self._build_run_ctx(run, emit)
            override = self._overrides.get(run_id)

            # confirm_check：auto_confirm=True → None（写工具直接执行，仍走幂等短路）；
            # auto_confirm=False → 写工具拦截、发 tool_confirm_required 并挂起。
            def _check(call: dict) -> bool:
                return needs_confirmation(
                    ctx.registry, call.get("tool_id", ""), run.auto_confirm,
                )
            confirm_check = None if run.auto_confirm else _check

            # 运行期超时（保留 autonomous_loop 的 loop_deadline 语义）
            config = get_config()
            deadline = (
                time.time()
                + config.loop_base_timeout
                + ctx.max_rounds * config.loop_per_round_timeout
            )

            # 2) 单步推进直到非 running
            while state.status == "running":
                state = await step_once(
                    state, ctx,
                    emit=emit,
                    llm_override=override,
                    confirm_check=confirm_check,
                    deadline=deadline,
                )
                # 协作式暂停：本轮已完整收尾（整轮 tool 响应齐备、状态自洽）。若收到 pause
                # 信号且 step_once 未把 run 推到非 running 终态，则把状态改 paused 再 save，
                # while 自然退出。step_once 已推到终态/挂起时 pause 不覆盖（尊重既有终态）。
                if run_id in self._pause_requested and state.status == "running":
                    self._pause_requested.discard(run_id)
                    state.status = "paused"
                async with self._session_factory() as s:
                    await repo.save_state(s, run_id, state)

            # 3) 终态分支收尾（codex P0）
            await self._emit_terminal(run_id, state, emit)

        except Exception as e:  # noqa: BLE001 — 兜底：任何异常都标 failed，不外传
            logger.exception("[RunController] run %s failed: %s", run_id, e)
            # 标 failed 终态：尽力 save_state（载入失败时也要把 run 标 failed）
            try:
                async with self._session_factory() as s:
                    fail_state = await repo.get_state(s, run_id)
                    if fail_state is not None:
                        fail_state.status = "failed"
                        await repo.save_state(s, run_id, fail_state)
            except Exception:  # noqa: BLE001
                logger.exception("[RunController] run %s save failed-state error", run_id)
            try:
                await emit({"type": EventType.ERROR, "error": str(e)})
            except Exception:  # noqa: BLE001
                logger.exception("[RunController] run %s emit ERROR failed", run_id)
            self._overrides.pop(run_id, None)
            self._sciverse_overrides.pop(run_id, None)

    async def _emit_terminal(
        self,
        run_id: int,
        state: LoopState,
        emit: Callable[[dict], Awaitable[None]],
    ) -> None:
        """按 state.status 发终态事件（done/failed/cancelled）；非终态不发、不清 override。

        awaiting_confirmation / paused：循环内已 save_state，**不发任何终态事件**（流保持
        打开，等待 confirm/resume）。done/failed/cancelled：发对应事件 + 清 override。
        """
        status = normalize_run_status(state.status)
        if status == "done":
            await emit({
                "type": EventType.RUN_COMPLETE,
                "status": status,
                "final_output": state.final_output,
            })
            self._overrides.pop(run_id, None)
            self._sciverse_overrides.pop(run_id, None)
        elif status == "failed":
            await emit({"type": EventType.ERROR, "error": state.final_output or ""})
            self._overrides.pop(run_id, None)
            self._sciverse_overrides.pop(run_id, None)
        elif status == "cancelled":
            await emit({"type": EventType.CANCELLED, "status": status})
            self._overrides.pop(run_id, None)
            self._sciverse_overrides.pop(run_id, None)
        elif status == "paused":
            # 非终态：发 paused 事件供前端展示，但 SSE 不据此关流（等 resume），override 保留。
            await emit({"type": EventType.PAUSED, "status": status})
        # awaiting_confirmation：不发任何事件（等 confirm，流保持打开），override 保留。

    async def confirm(self, run_id: int, tool_call_id: str, decision: str) -> str:
        """处理一个 awaiting_confirmation run 的队首确认决定，必要时续跑。

        Args:
            run_id: run id。
            tool_call_id: 待确认的工具调用 id，必须 == pending_round.queue[0]。
            decision: "approve" | "reject"。

        Returns:
            处理后的 run 状态字符串（awaiting_confirmation = 还有下一个写工具待确认；
            running/done/failed/... = 协议完成已续跑后的终态）。

        Raises:
            ApiError(409, CONFIRM_*): 状态非 awaiting_confirmation / 无 pending_round /
                tool_call_id 与队首不符。
        """
        # 1) 载入状态 + 前置校验。
        async with self._session_factory() as s:
            run = await repo.get_run(s, run_id)
            if run is None:
                raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {run_id} 不存在")
            state = await repo.get_state(s, run_id)

        if state is None or state.status != "awaiting_confirmation" or not state.pending_round:
            raise ApiError(409, "CONFIRM_NOT_AWAITING", "该 run 当前不在待确认状态")

        queue = state.pending_round.get("queue") or []
        if not queue or queue[0].get("tool_call_id") != tool_call_id:
            head_id = queue[0].get("tool_call_id") if queue else None
            raise ApiError(
                409, "CONFIRM_OUT_OF_ORDER",
                f"确认顺序错误：当前待确认 {head_id!r}，收到 {tool_call_id!r}",
            )

        # 2) 构建 emit（哈希链）+ 按 run 注入 ctx（与 _drive 同源）。
        emit = self._make_emit(run_id)
        ctx = await self._build_run_ctx(run, emit)

        # 3) 消费队列（步骤 4-6 在 engine.apply_confirmation 内）。
        state = await apply_confirmation(
            state, ctx, tool_call_id, decision, emit=emit,
        )

        # 4) 落库新状态。
        async with self._session_factory() as s:
            await repo.save_state(s, run_id, state)

        # 5) 仍 awaiting → 等下一次 confirm；协议完成（running）→ 后台续跑驱动。
        if state.status == "running":
            self.start(
                run_id,
                llm_override=self._overrides.get(run_id),
                sciverse_override=self._sciverse_overrides.get(run_id),
            )
        return state.status

    # ------------------------------------------------------------------
    # 运行生命周期：pause / resume / cancel / shutdown / recover_orphans（P3-1）
    # ------------------------------------------------------------------

    async def pause(self, run_id: int) -> str:
        """请求暂停一个运行中的 run（协作式）。

        语义：设置内存 pause 信号 → 驱动循环（_drive）在**当前轮完整收尾后**自然退出
        （把 state.status 置 paused 并 save，不发终态、流保持打开）。同时把 DB status
        写成 paused，覆盖「pause 时无活跃 task」（如刚好在两次驱动间）的情形——此时
        没有 _drive 会去落 paused，需端点直接写。

        仅对 running 的 run 生效；非 running（已终态/awaiting）原样返回当前状态。
        Raises: ApiError(404) run 不存在。
        """
        async with self._session_factory() as s:
            run = await repo.get_run(s, run_id)
            if run is None:
                raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {run_id} 不存在")
            status = normalize_run_status(run.status)
            if status != "running":
                return status
            self._pause_requested.add(run_id)
            # 无活跃 task（两次驱动之间）→ 端点直接落 paused；有活跃 task 时 _drive 的
            # 协作退出也会落同一状态（幂等，不竞态：单任务串行驱动）。
            state = await repo.get_state(s, run_id)
            if state is not None:
                state.status = "paused"
                await repo.save_state(s, run_id, state)
        return "paused"

    async def resume(self, run_id: int) -> str:
        """恢复一个 paused 的 run：状态拉回 running + 后台续跑（复用 start）。

        续跑沿用内存 _overrides 里保存的 per-run LLM 覆盖（服务重启会丢，退回平台默认 key，
        与 confirm 续跑同口径）。仅对 paused 的 run 生效；非 paused 原样返回当前状态。
        Raises: ApiError(404) run 不存在。
        """
        # 初次校验（只读，不写）：仅 paused 的 run 才有恢复语义；非 paused 直接返回。
        # 关键（codex P0）：此处**不**急着把状态拉回 running——pause 与 resume 之间、
        # 以及下面 await prev 期间，仍在跑的 _drive 可能把 run 推到终态。过早改 running
        # 会与 _drive 的终态写竞态、并丢失「已终态」信息。改 running 推迟到 await prev
        # 之后、确认仍 paused 再做。
        async with self._session_factory() as s:
            run = await repo.get_run(s, run_id)
            if run is None:
                raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {run_id} 不存在")
            status = normalize_run_status(run.status)
            if status != "paused":
                return status

        # 等上一段驱动 task 真正结束再重启：pause 退出后 _drive task 可能尚未被 done_callback
        # 从 _tasks 清理（task 已收尾但回调未跑），此时 start() 会因「task 未 done」误判为
        # 仍在运行而跳过续跑 → run 卡死 paused。先 await 它收束，确保 start 能起新 task。
        #
        # codex P0 注意：**不能在 await prev 之前 discard pause 信号**——若上一段 _drive
        # 仍在 step_once 内（pause 端点已写 DB=paused，但 _drive 还没轮到 while 检查 pause
        # 信号），提前 discard 会让它错过 pause 协作退出、继续跑到 done/failed。必须先
        # await 它按 pause 信号收束，再 discard（为本次续跑清场）。
        prev = self._tasks.get(run_id)
        if prev is not None and not prev.done():
            try:
                await prev
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — _drive 内部已兜底，这里防御性吞
                logger.exception("[RunController] run %s resume-await prev task error", run_id)

        # prev 已收束 → 现在清 pause 信号，避免本次续跑被旧信号误暂停。
        self._pause_requested.discard(run_id)

        # codex P0 竞态修复：await prev 期间仍在跑的 _drive 可能已把 run 推到终态
        # （done/failed/cancelled）。必须在 await prev 之后**重新读取**真实 status：
        #   - 仍是 paused → 此刻才把状态拉回 running + save，emit(resumed) + start 续跑；
        #   - 已是终态 → 不重启、不发 resumed，直接返回该终态状态，避免重新驱动已终态 run
        #     造成重复 LLM/工具副作用。
        async with self._session_factory() as s:
            run = await repo.get_run(s, run_id)
            if run is None:
                raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {run_id} 不存在")
            status = normalize_run_status(run.status)
            if status != "paused":
                return status
            state = await repo.get_state(s, run_id)
            if state is not None:
                state.status = "running"
                await repo.save_state(s, run_id, state)

        # 发 resumed 事件供前端展示（非终态）。再后台续跑驱动。
        emit = self._make_emit(run_id)
        await emit({"type": EventType.RESUMED, "status": "running"})
        self.start(
            run_id,
            llm_override=self._overrides.get(run_id),
            sciverse_override=self._sciverse_overrides.get(run_id),
        )
        return "running"

    async def cancel(self, run_id: int) -> str:
        """取消一个 run（终态）：标 status=cancelled + 取消活跃 _drive task + 发 cancelled 终态。

        正确性要点（codex 并发）：cancel 活跃 task 会向 _drive 注入 asyncio.CancelledError；
        CancelledError 继承自 BaseException（非 Exception），_drive 的 `except Exception`
        不会把它误标 failed——cancel 的终态是 cancelled。task 取消后由本方法权威落
        cancelled 状态并 emit(cancelled)，不依赖被取消的 _drive 走 _emit_terminal。

        已处终态（done/failed/cancelled）→ 原样返回当前状态（幂等，不重复发终态）。
        Raises: ApiError(404) run 不存在。
        """
        async with self._session_factory() as s:
            run = await repo.get_run(s, run_id)
            if run is None:
                raise ApiError(404, "RUN_NOT_FOUND", f"AgentRun {run_id} 不存在")
            if is_terminal_run_status(run.status):
                return normalize_run_status(run.status)

        # 1) 取消活跃 task（若有）。cancel() 仅请求取消；task 实际结束由 _on_done 清理。
        self._pause_requested.discard(run_id)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            # 等被取消的 task 真正结束（吞掉 CancelledError），避免与下面落库竞态。
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — _drive 内部已兜底，这里防御性吞
                logger.exception("[RunController] run %s cancel-await error", run_id)

        # 2) 权威落 cancelled 终态 + 发终态事件 + 清 override。
        async with self._session_factory() as s:
            state = await repo.get_state(s, run_id)
            if state is None:
                state = LoopState(messages=[])
            state.status = "cancelled"
            await repo.save_state(s, run_id, state)
        emit = self._make_emit(run_id)
        await emit({"type": EventType.CANCELLED, "status": "cancelled"})
        self._overrides.pop(run_id, None)
        self._sciverse_overrides.pop(run_id, None)
        return "cancelled"

    async def shutdown(self) -> None:
        """服务停机：取消所有活跃 _drive task 并等待其结束。

        lifespan 退出时调用。对每个 task 发 cancel()，再 gather(return_exceptions=True)
        等全部收束。CancelledError 需单独 re-raise——若 shutdown 本身被取消（外层关停
        超时），不能被当成普通任务异常吞掉，要把取消语义如实向外传播。
        """
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        if not tasks:
            return
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            # shutdown 自身被取消（而非被取消的子 task）→ 如实传播，不吞。
            raise

    async def recover_orphans(self, session) -> int:
        """启动时回收孤儿 run：把残留 status=running 的 run 标为 failed，并补发终态事件。

        进程上次崩溃/重启时，正在 running 的 run 的驱动 task 已随进程消失，但 DB 仍记
        running——这些是孤儿。启动时统一标 failed（无活跃 task 续跑它们，保持 running 会
        误导前端永久 spinner / SSE 空等）。返回回收数量。

        codex P1：仅改 DB status 还不够——SSE 端点据「历史/实时事件里的终态事件」收敛，
        孤儿 run 历史里没有终态事件、又没有活跃 task 会再发事件，客户端连上去会永久
        heartbeat 空等。故标 failed 后**逐个用 append_event_chained 追加一条 error 终态
        事件**（保持哈希链完整），让 SSE 历史里有终态可收敛。

        Args:
            session: 调用方提供的 AsyncSession（lifespan 用一个 session）。
        """
        from sqlalchemy import select
        from ..models import AgentRun

        rows = (await session.execute(
            select(AgentRun).where(AgentRun.status == "running")
        )).scalars().all()
        orphan_ids = [run.id for run in rows]
        for run in rows:
            run.status = "failed"
        if not orphan_ids:
            return 0
        await session.commit()

        # 逐个补发 error 终态事件（哈希链）。用调用方 session（append_event_chained 内部
        # 自管 commit/rollback）。单条事件失败不应影响其它孤儿的回收，逐个 try。
        for run_id in orphan_ids:
            try:
                await repo.append_event_chained(
                    session, run_id, EventType.ERROR,
                    {
                        "type": EventType.ERROR,
                        "error": "服务重启时残留 running，已标记 failed",
                    },
                )
            except Exception:  # noqa: BLE001 — 补发事件尽力而为，不阻断回收
                logger.exception(
                    "[RunController] recover_orphans: run %s append terminal event failed",
                    run_id,
                )
        logger.info("[RunController] recover_orphans: %d run(s) marked failed", len(orphan_ids))
        return len(orphan_ids)

    def _on_done(self, task: asyncio.Task) -> None:
        """Task 完成回调：清理 _tasks + 记录未捕获异常（不能 await）。

        _drive 内已 try/except 标 failed，理论上 task 不会带异常；这里仅作
        最后防线：若仍有未捕获异常，记录日志（避免 'Task exception was never
        retrieved' 警告），并从 _tasks 移除以释放强引用。
        """
        # 找回 run_id（task 即 value）并移除。
        for run_id, t in list(self._tasks.items()):
            if t is task:
                self._tasks.pop(run_id, None)
                break
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("[RunController] background task crashed: %r", exc)
