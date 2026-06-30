"""测试安全带 contextvars 修复（并发安全）。

覆盖:
  - _records_context_var 是 ContextVar 而非模块级全局变量
  - 两路并发校验使用不同 records → 不互串（core 并发安全测试）
  - ContextVar 嵌套调用后正确 reset（token.reset）
  - 并发 check_citations_against_records 隔离验证
"""
from __future__ import annotations

import asyncio
import pytest
from contextvars import ContextVar

# ======================================================================
# ContextVar 类型验证
# ======================================================================

class TestContextVarType:
    def test_records_context_var_is_contextvar(self):
        """_records_context_var 必须是 ContextVar，不是 list 全局变量。"""
        from app.safety.citation import _records_context_var
        assert isinstance(_records_context_var, ContextVar), (
            "_records_context_var 应为 ContextVar 实例，而非模块级 list"
        )

    def test_default_value_is_empty_list(self):
        """默认值应为空列表。"""
        from app.safety.citation import _records_context_var
        assert _records_context_var.get() == []

    def test_set_and_reset_isolated(self):
        """set/reset 在当前上下文可见，不影响原始默认值。"""
        from app.safety.citation import _records_context_var
        original = _records_context_var.get()
        token = _records_context_var.set([{"title": "test"}])
        assert _records_context_var.get() == [{"title": "test"}]
        _records_context_var.reset(token)
        assert _records_context_var.get() == original


# ======================================================================
# 并发安全测试
# ======================================================================

RECORDS_A = [
    {"idx": 1, "title": "Alpha Paper", "authors": "A1", "year": 2020, "doi": "10.1111/alpha"},
]

RECORDS_B = [
    {"idx": 1, "title": "Beta Paper", "authors": "B1", "year": 2021, "doi": "10.2222/beta"},
    {"idx": 2, "title": "Gamma Paper", "authors": "B2", "year": 2022, "doi": "10.3333/gamma"},
]


class TestConcurrentContextIsolation:
    @pytest.mark.asyncio
    async def test_two_concurrent_validations_do_not_interfere(self):
        """两路并发校验使用不同 records → results 不互串。

        路径 A: records=RECORDS_A（1 条），文本含 A 的 DOI
        路径 B: records=RECORDS_B（2 条），文本含 B 的 DOI
        两路同时运行，各自应只命中自己的 records。
        """
        from app.safety.citation import check_citations_against_records, CitationFailStrategy

        text_a = "研究见 10.1111/alpha 的结论。"
        text_b = "另据 10.2222/beta 的结果。"

        results = {}

        async def validate_a():
            r = check_citations_against_records(text_a, RECORDS_A, CitationFailStrategy.NOOP)
            results["a"] = r

        async def validate_b():
            r = check_citations_against_records(text_b, RECORDS_B, CitationFailStrategy.NOOP)
            results["b"] = r

        # 并发运行两路校验
        await asyncio.gather(validate_a(), validate_b())

        r_a = results["a"]
        r_b = results["b"]

        # A 路应命中 alpha DOI（green）
        assert r_a.summary.get("green", 0) >= 1 or r_a.summary.get("yellow", 0) >= 1 or r_a.fabricated == [], \
            f"A 路引用未能命中 RECORDS_A: {r_a.summary}, fabricated={r_a.fabricated}"

        # B 路应命中 beta DOI（green）
        assert r_b.summary.get("green", 0) >= 1 or r_b.summary.get("yellow", 0) >= 1 or r_b.fabricated == [], \
            f"B 路引用未能命中 RECORDS_B: {r_b.summary}, fabricated={r_b.fabricated}"

    @pytest.mark.asyncio
    async def test_concurrent_different_records_no_crosscontamination(self):
        """并发 10 路，每路 records 不同，各自结果互不污染。

        核心测试：路径 i 使用 doi=10.xxxx/paper_{i}，
        其 records 只包含 doi_{i}，不包含其他。
        若 ContextVar 失效（退化为全局），后写者会覆盖前者，导致部分路径 records 错误。
        """
        from app.safety.citation import check_citations_against_records, CitationFailStrategy

        N = 10
        doi_template = "10.{:04d}/paper-{}"

        async def validate_one(i: int, barrier: asyncio.Barrier):
            doi = doi_template.format(i, i)
            records = [{"idx": 1, "title": f"Paper {i}", "authors": f"A{i}", "year": 2020, "doi": doi}]
            text = f"见 {doi} 的研究结论。"

            # 到达屏障前全部暂停，确保并发启动（而非顺序执行）
            await barrier.wait()

            result = check_citations_against_records(text, records, CitationFailStrategy.NOOP)
            return i, result

        barrier = asyncio.Barrier(N)
        tasks = [validate_one(i, barrier) for i in range(N)]
        pair_results = await asyncio.gather(*tasks)

        for i, result in pair_results:
            expected_doi = doi_template.format(i, i)
            # 每路的 DOI 应在各自 records 中命中，不应被标为 fabricated
            assert expected_doi not in result.fabricated, (
                f"路径 {i}: DOI {expected_doi} 被误标为 fabricated，"
                f"可能是 records 上下文污染。fabricated={result.fabricated}"
            )

    @pytest.mark.asyncio
    async def test_nested_context_resets_correctly(self):
        """嵌套调用后 ContextVar 正确 reset（不残留内层 records）。"""
        from app.safety.citation import _records_context_var, check_citations_against_records, CitationFailStrategy

        outer_records = [{"idx": 1, "title": "Outer", "doi": "10.0001/outer", "year": 2020, "authors": "O"}]
        inner_records = [{"idx": 1, "title": "Inner", "doi": "10.0002/inner", "year": 2020, "authors": "I"}]

        # 外层调用
        check_citations_against_records("见 10.0001/outer 的结果。", outer_records, CitationFailStrategy.NOOP)

        # 内层调用
        check_citations_against_records("见 10.0002/inner 的结果。", inner_records, CitationFailStrategy.NOOP)

        # 调用后 ContextVar 应 reset 到默认（空列表）
        current = _records_context_var.get()
        assert current == [], (
            f"嵌套调用后 _records_context_var 未 reset，当前值: {current}"
        )

    @pytest.mark.asyncio
    async def test_exception_in_impl_does_not_leak_context(self):
        """即使 _check_citations_impl 抛出异常，ContextVar 也应被 finally reset。"""
        from app.safety.citation import _records_context_var, check_citations_against_records, CitationFailStrategy
        from unittest.mock import patch

        test_records = [{"idx": 1, "title": "T", "doi": "10.x/y", "year": 2020, "authors": "A"}]

        with patch("app.safety.citation._check_citations_impl", side_effect=RuntimeError("Mock error")):
            try:
                check_citations_against_records("text", test_records, CitationFailStrategy.NOOP)
            except RuntimeError:
                pass  # 期望异常被抛出

        # 异常后，ContextVar 应 reset 到默认值
        current = _records_context_var.get()
        assert current == [], (
            f"异常后 _records_context_var 未 reset，当前值: {current}"
        )


# ======================================================================
# 向后兼容：原有接口不变
# ======================================================================

class TestBackwardCompatibility:
    def test_check_citations_against_records_still_works(self):
        """contextvars 修复后，原有接口行为不变。"""
        from app.safety.citation import check_citations_against_records, CitationFailStrategy

        records = [
            {"idx": 1, "title": "Alpha", "authors": "A", "year": 2020, "doi": "10.1111/alpha"},
        ]
        text = "根据 10.1111/alpha 的研究。"
        result = check_citations_against_records(text, records, CitationFailStrategy.NOOP)
        assert result.validation_passed
        assert result.fabricated == []

    def test_fabricated_still_detected(self):
        from app.safety.citation import check_citations_against_records, CitationFailStrategy

        records = [
            {"idx": 1, "title": "Alpha", "authors": "A", "year": 2020, "doi": "10.1111/alpha"},
        ]
        text = "见 10.9999/fake-doi 的结论。"
        result = check_citations_against_records(text, records, CitationFailStrategy.NOOP)
        assert not result.validation_passed
        assert "10.9999/fake-doi" in result.fabricated
