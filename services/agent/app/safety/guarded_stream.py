"""GuardedStream — 受控 token 流, 在句/章边界校验引用后放行。

架构说明 (阶段 4b 技术决策):
  Guardrails AI 0.10.x 的流式 API 是面向 LLM API 调用层 (依赖 LiteLLM/OpenAI 包装),
  其 Guard.parse(stream=True) 需要传入 LiteLLM callable, 不能直接包装
  harness.llm.stream_content 返回的 token-level AsyncIterator[str]。
  因此 GuardedStream 采用"薄自实现缓冲 + Guardrails Guard.validate 调用"的方式:
    1. 自实现: 缓冲 token 到句/章边界 (中英文句号/段落分隔符)
    2. Guardrails: 在每个缓冲段上调用 Guard.validate() (即 CitationExistenceValidator)
    3. 自实现: 根据策略决定放行/标红/拒绝

这样既保留了 Guardrails AI 作为校验框架的核心角色, 又干净地解决了流式适配问题。

三条"安全带"语义:
  - 伪造引用在放行前被拦/标记: GuardedStream 不会吐出未经校验的段落
  - 句/章边界缓冲: token 在 _buffer 内积累, 达到边界才校验并 yield
  - 校验器崩溃 fail-closed (codex P0-2): 校验器抛非预期异常时, 绝不降级放行未校验原文,
    改抛 ValidationUnavailableError 中断流 (宁可整份综述失败也不放出未校验的"可信"文本)

用法示例:
    async def my_token_stream():
        for token in ["Smith (2020) ", "says ...", "10.xxx/fake "]:
            yield token

    stream = GuardedStream(
        token_stream=my_token_stream(),
        records=[...],
        strategy="annotate",
    )
    async for chunk in stream:
        print(chunk, end="", flush=True)
    print("\\n--- metadata ---")
    print(stream.evidence_refs)
    print(stream.fabricated_spans)
"""
from __future__ import annotations

import re
import logging
from typing import AsyncIterator, Optional

from .citation import check_citations_against_records, CitationFailStrategy
from .evidence import EvidenceRef

logger = logging.getLogger("agent_safety.guarded_stream")

# 句/章边界识别正则 (中英文句号、段落分隔)
_SENT_BOUNDARY = re.compile(
    r"(?<=[。！？.!?])\s+"          # 中英文句末 + 后续空白
    r"|(?<=\n)\s*\n"                # 空行 (段落分隔)
    r"|(?<=[。！？.!?])\n"          # 句末换行
)

# 最大缓冲长度 (tokens 堆积超过此字符数时强制刷新, 避免无句号段落无限堆积)
_MAX_BUFFER_CHARS = 800


class FabricatedCitationError(RuntimeError):
    """当 strategy=REJECT 且段落中有伪造引用时抛出。"""

    def __init__(self, fabricated: list[str], segment: str):
        self.fabricated = fabricated
        self.segment = segment
        super().__init__(
            f"GuardedStream: 检测到 {len(fabricated)} 条疑似伪造引用 "
            f"({', '.join(repr(f) for f in fabricated[:3])}), 拒绝放行该段。"
        )


class ValidationUnavailableError(RuntimeError):
    """校验器不可用/崩溃, 无法保证引用可信 → fail-closed 信号。

    codex P0-2：原实现里, ANNOTATE/NOOP 策略下 check_citations_against_records 抛异常时,
    GuardedStream 会"降级放行"未校验原文 (yield segment) 且不计伪造 —— 这是安全带旁路,
    把未校验文本当成已校验的可信 review 放出去, 破坏"引用经校验"的核心 claim。

    修复语义 (fail-closed)：校验器对某段抛任何非预期异常时, GuardedStream *不再* 放行
    该段原文, 而是抛 ValidationUnavailableError 中断整个流。上游 (generate_review) 捕获后
    产出 error 事件, ReviewTool 据此不发 review_complete、ToolResult success=False ——
    宁可整份综述失败, 也绝不输出未经校验的"可信"综述。
    """

    def __init__(self, segment: str, cause: Exception | None = None):
        self.segment = segment
        self.cause = cause
        super().__init__(
            f"GuardedStream: 引用校验器不可用 (异常: {cause!r}), "
            f"fail-closed 拒绝放行未校验段落 (len={len(segment)})。"
        )


class GuardedStream:
    """把一个 token 异步流包装为"先缓冲到句/章边界, 再校验引用, 通过则放行"的受控流。

    Args:
        token_stream: AsyncIterator[str], 逐 token 的原始内容流
        records:      语料记录列表 [{title, authors, year, doi, ...}]
        strategy:     失败策略 (annotate/reject/noop), 默认 annotate
        corpus_id:    语料标识, 写入 EvidenceRef

    Attributes (消费完流后可读):
        evidence_refs:    所有放行段产出的 EvidenceRef 列表 (命中语料的引用)
        fabricated_spans: 所有检测到的疑似伪造引用字符串
        segments_checked: 经过校验的段数
        segments_blocked: 被拒绝的段数 (strategy=reject 时)
    """

    def __init__(
        self,
        token_stream: AsyncIterator[str],
        records: list[dict],
        strategy: str = CitationFailStrategy.ANNOTATE,
        corpus_id: str = "local_corpus",
    ):
        self._stream = token_stream
        self._records = records
        self._strategy = strategy
        self._corpus_id = corpus_id
        self._buffer: str = ""

        # 消费后的统计/产出
        self.evidence_refs: list[EvidenceRef] = []
        self.fabricated_spans: list[str] = []
        self.segments_checked: int = 0
        self.segments_blocked: int = 0

    # ------------------------------------------------------------------
    # 主接口
    # ------------------------------------------------------------------

    async def __aiter__(self) -> AsyncIterator[str]:
        """异步迭代器: 逐段放行经过校验的文本块。"""
        async for token in self._stream:
            self._buffer += token
            # 检查缓冲区是否达到句/章边界
            released = self._try_flush(force=False)
            if released:
                async for chunk in self._validate_and_yield(released):
                    yield chunk

        # 流结束后强制刷新剩余缓冲
        if self._buffer.strip():
            released = self._try_flush(force=True)
            if released:
                async for chunk in self._validate_and_yield(released):
                    yield chunk

    # ------------------------------------------------------------------
    # 内部: 缓冲分段
    # ------------------------------------------------------------------

    def _try_flush(self, force: bool = False) -> Optional[str]:
        """尝试从缓冲区切出一个完整段落。

        Returns:
            切出的段落文本, 若暂无完整段落则返回 None。
        """
        buf = self._buffer

        if force:
            if buf:
                self._buffer = ""
                return buf
            return None

        # 按句/章边界找最后一个分隔点
        matches = list(_SENT_BOUNDARY.finditer(buf))
        if matches:
            last = matches[-1]
            segment = buf[:last.end()]
            self._buffer = buf[last.end():]
            return segment

        # 超长缓冲强制切割 (避免无句号段落无限堆积)
        if len(buf) >= _MAX_BUFFER_CHARS:
            # 在最后一个空格处切割, 避免切断单词
            cut = buf.rfind(" ", 0, _MAX_BUFFER_CHARS)
            if cut <= 0:
                cut = _MAX_BUFFER_CHARS
            segment = buf[:cut + 1]
            self._buffer = buf[cut + 1:]
            return segment

        return None

    # ------------------------------------------------------------------
    # 内部: 校验 + 放行
    # ------------------------------------------------------------------

    async def _validate_and_yield(self, segment: str) -> AsyncIterator[str]:
        """对一个段落运行引用校验, 根据策略决定放行/标红/拒绝。

        异常语义 (codex P0-2, fail-closed)：
          - FabricatedCitationError: 直接向上传播 (REJECT 策略的伪造拒绝)。
          - 校验器其他异常 (Guardrails ValidationError 之外的崩溃):
              * REJECT 策略  → 转换为 FabricatedCitationError 拒绝该段 (原行为)。
              * 其他策略     → *不再* 降级放行未校验原文; 改抛 ValidationUnavailableError
                              中断整个流。绝不把未校验文本作为已校验 review 放行。
        """
        self.segments_checked += 1

        try:
            result = check_citations_against_records(
                text=segment,
                records=self._records,
                strategy=self._strategy,
                corpus_id=self._corpus_id,
            )
        except FabricatedCitationError:
            # FabricatedCitationError 直接传播 (REJECT 策略由 citation.py 抛出后转换前向上)
            raise
        except Exception as e:
            if self._strategy == CitationFailStrategy.REJECT:
                # REJECT 策略: Guardrails 的 ValidationError / 其他校验异常 → 转换为 FabricatedCitationError
                # 尝试从 raw_result 中取 fabricated (通过二次调用 NOOP 拿数据)
                try:
                    from .citation import check_citations_against_records as _check
                    noop_result = _check(segment, self._records, strategy=CitationFailStrategy.NOOP)
                    fabricated = noop_result.fabricated
                except Exception:
                    fabricated = []
                self.segments_blocked += 1
                self.fabricated_spans.extend(fabricated)
                raise FabricatedCitationError(fabricated=fabricated, segment=segment) from e
            # 其他策略 (ANNOTATE/NOOP): 校验器崩溃 → fail-closed, 绝不放行未校验原文。
            # codex P0-2：抛 ValidationUnavailableError 中断流, 由 generate_review 转 error 事件。
            self.segments_blocked += 1
            logger.error(
                "[GuardedStream] 校验器异常 (fail-closed, 拒绝放行未校验段落): %s", e
            )
            raise ValidationUnavailableError(segment=segment, cause=e) from e

        # 累积全局统计
        self.evidence_refs.extend(result.evidence_refs)
        self.fabricated_spans.extend(result.fabricated)

        if not result.validation_passed and self._strategy == CitationFailStrategy.REJECT:
            self.segments_blocked += 1
            raise FabricatedCitationError(
                fabricated=result.fabricated,
                segment=segment,
            )

        # 放行: 输出 validated_output (FIX 策略下含警告注释, NOOP 下为原文)
        yield result.validated_output
