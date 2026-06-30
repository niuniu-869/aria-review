"""Task 4 — ReviewTool.paper_type + 单遍大纲注入测试

TDD: 先跑失败（_build_synthesis_messages 无 template 参数）→ 实现 → 跑通过。

测试约束（见 spec 关键设计决策 codex P1）：
  - 单遍注入：template 给定时 system 末尾含章节标题 + 抗幻觉指令。
  - template=None 时旧行为完全不变（旧 system 结构不破坏）。
  - FakeLLM 离线可跑（无需真实 API key）。
"""
from __future__ import annotations

import pytest
from app.review.templates import get_template, REVIEW_GROUNDING_DIRECTIVE
from app.review import synthesis


# ============================================================
# _build_synthesis_messages 注入测试
# ============================================================

def test_build_synthesis_messages_with_template_injects_outline():
    """template 给定时，system 应含章节标题和抗幻觉指令。"""
    template = get_template("phd")
    msgs = synthesis._build_synthesis_messages(
        topic="主题测试",
        summaries=[],
        skill_content="skill stub",
        template=template,
    )
    assert len(msgs) >= 2
    sys_content = msgs[0]["content"]
    # 博士模板 5 个章节标题都应出现
    assert "研究背景与理论基础" in sys_content
    assert "主题聚类与方法学进展" in sys_content
    assert "国外研究脉络与代表性成果" in sys_content
    assert "研究空白与本研究定位" in sys_content
    # 抗幻觉指令必须存在
    assert "抗幻觉" in sys_content


def test_build_synthesis_messages_without_template_unchanged():
    """template=None 时，旧 system 结构不破坏（有 system role + 旧约束）。"""
    msgs = synthesis._build_synthesis_messages(
        topic="主题测试",
        summaries=[],
        skill_content="skill stub",
        template=None,
    )
    assert len(msgs) >= 2
    assert msgs[0]["role"] == "system"
    # 旧约束：这些关键词依旧存在
    sys_content = msgs[0]["content"]
    assert "学术文献综述" in sys_content


def test_render_outline_contains_all_chapters():
    """_render_outline 应包含模板所有章节标题。"""
    template = get_template("undergrad")
    rendered = synthesis._render_outline(template)
    assert "本科毕业论文综述" in rendered
    assert "研究背景与意义" in rendered
    assert "国内外研究现状" in rendered
    assert "研究述评与展望" in rendered
    assert REVIEW_GROUNDING_DIRECTIVE in rendered


def test_render_outline_sci_intro_english():
    """SCI 论型英文章节也正确渲染。"""
    template = get_template("sci_intro")
    rendered = synthesis._render_outline(template)
    assert "Background and motivation" in rendered
    assert "Literature gap" in rendered
    assert "Contribution and structure" in rendered


def test_build_synthesis_messages_template_none_no_injection():
    """template=None 时，system 中不能出现论型章节大纲相关文字。"""
    msgs = synthesis._build_synthesis_messages(
        topic="主题",
        summaries=[],
        skill_content="skill stub",
        template=None,
    )
    sys_content = msgs[0]["content"]
    # 这些是模板大纲特有的标记词，不应出现在 template=None 的 system 中
    assert "论型:" not in sys_content
    assert "章节大纲" not in sys_content


# ============================================================
# generate_review 签名兼容测试（离线 FakeLLM）
# ============================================================

@pytest.mark.asyncio
async def test_generate_review_accepts_template_param():
    """generate_review 应接受 template 参数（不报 TypeError）。"""
    template = get_template("master")
    events = []
    async for ev in synthesis.generate_review(
        topic="测试主题",
        summaries=[],
        records=[],
        template=template,
    ):
        events.append(ev)
    # 无 summaries 时应返回 error 事件（旧语义不变）
    assert any(e.event == "error" for e in events)


@pytest.mark.asyncio
async def test_generate_review_without_template_still_works():
    """template=None（默认）时旧行为不变（error 因 summaries 为空）。"""
    events = []
    async for ev in synthesis.generate_review(
        topic="测试主题",
        summaries=[],
        records=[],
    ):
        events.append(ev)
    assert any(e.event == "error" for e in events)


# ============================================================
# _build_group_summary_messages 与 _build_meta_synthesis_messages
# template=None 时不报错（向后兼容）
# ============================================================

def test_build_group_summary_messages_no_template_param():
    """分组小结 builder 中间层干净，无 template 参数（I1 修复验证）。"""
    msgs = synthesis._build_group_summary_messages(
        topic="主题",
        group_summaries=[],
        global_start_idx=1,
        skill_content="skill stub",
    )
    assert msgs[0]["role"] == "system"


def test_build_meta_synthesis_messages_template_none():
    """meta 合成 builder template=None 时不报错。"""
    msgs = synthesis._build_meta_synthesis_messages(
        topic="主题",
        group_mini_reviews=["小结1"],
        records=[],
        skill_content="skill stub",
        template=None,
    )
    assert msgs[0]["role"] == "system"


# ============================================================
# run_review 透传测试
# ============================================================

@pytest.mark.asyncio
async def test_run_review_accepts_template_param():
    """run_review 应接受 template 参数（不报 TypeError）。"""
    from app.review.orchestrate import run_review
    template = get_template("grant")
    # paper_markdowns 为空 → 直接完成（但函数签名不应报错）
    result = await run_review(
        topic="测试",
        paper_markdowns=[],
        records=[],
        template=template,
    )
    # 结果应是 dict（含 review_md、stats 等字段）
    assert isinstance(result, dict)
    assert "review_md" in result


@pytest.mark.asyncio
async def test_run_review_without_template_backward_compat():
    """run_review 不传 template 时旧行为完全不变。"""
    from app.review.orchestrate import run_review
    result = await run_review(
        topic="测试",
        paper_markdowns=[],
        records=[],
    )
    assert isinstance(result, dict)
    assert "review_md" in result


# ============================================================
# C1: user 消息与模板大纲冲突修复测试
# ============================================================

def test_build_synthesis_messages_template_user_no_fixed_structure():
    """C1: template 给定时，user 消息不应包含固定 6 节结构指令。

    模板给定时 LLM 应按模板大纲写，user 消息里不能再出现
    "引言/主题归纳/方法分布" 这类固定结构约束词，否则 LLM 会忽略模板。
    """
    template = get_template("phd")
    msgs = synthesis._build_synthesis_messages(
        topic="测试主题",
        summaries=[],
        skill_content="skill stub",
        template=template,
    )
    user_content = msgs[1]["content"]
    # 这些是固定 6 节结构的关键词，template 给定时不应出现在 user 消息里
    assert "引言/主题归纳/方法分布" not in user_content
    assert "主题归纳/方法分布" not in user_content
    # template 给定时应出现"章节大纲"类指令
    assert "章节大纲" in user_content or "上述" in user_content


def test_build_synthesis_messages_no_template_user_has_fixed_structure():
    """C1 (反面): template=None 时，user 消息应保留原 6 节固定结构指令。"""
    msgs = synthesis._build_synthesis_messages(
        topic="测试主题",
        summaries=[],
        skill_content="skill stub",
        template=None,
    )
    user_content = msgs[1]["content"]
    # template=None 时应保留原 6 节约束
    assert "引言" in user_content
    assert "主题归纳" in user_content
    assert "方法分布" in user_content


def test_build_meta_synthesis_messages_template_user_no_fixed_structure():
    """C1: meta 路径 template 给定时，user 消息不应包含固定 6 节结构指令。"""
    template = get_template("master")
    msgs = synthesis._build_meta_synthesis_messages(
        topic="测试主题",
        group_mini_reviews=["小结1"],
        records=[],
        skill_content="skill stub",
        template=template,
    )
    user_content = msgs[1]["content"]
    # template 给定时 user 消息不应含固定 6 节结构约束
    assert "引言/主题归纳/方法分布" not in user_content
    assert "主题归纳/方法分布" not in user_content
    # 应出现模板大纲类指令
    assert "章节大纲" in user_content or "上述" in user_content


def test_build_meta_synthesis_messages_no_template_user_has_fixed_structure():
    """C1 (反面): meta 路径 template=None 时，user 消息应保留原 6 节固定结构指令。"""
    msgs = synthesis._build_meta_synthesis_messages(
        topic="测试主题",
        group_mini_reviews=["小结1"],
        records=[],
        skill_content="skill stub",
        template=None,
    )
    user_content = msgs[1]["content"]
    # template=None 时应保留原 6 节约束
    assert "引言" in user_content
    assert "主题归纳" in user_content
    assert "方法分布" in user_content


# ============================================================
# A: meta system 提示词"6 节"冲突修复测试（A项修复验证）
# ============================================================

def test_build_meta_synthesis_messages_template_system_no_6_sections():
    """A项: template 给定时，meta system 不应含"6 节"字样（与章节大纲冲突）。"""
    template = get_template("phd")
    msgs = synthesis._build_meta_synthesis_messages(
        topic="测试主题",
        group_mini_reviews=["小结1"],
        records=[],
        skill_content="skill stub",
        template=template,
    )
    sys_content = msgs[0]["content"]
    # template 给定时 system 不能有"6 节"硬编码（章节由模板大纲决定）
    assert "6 节" not in sys_content
    # 模板章节标题应注入到 system
    assert "研究背景与理论基础" in sys_content


def test_build_meta_synthesis_messages_no_template_system_has_6_sections():
    """A项 (反面): template=None 时，meta system 应保留"6 节"措辞。"""
    msgs = synthesis._build_meta_synthesis_messages(
        topic="测试主题",
        group_mini_reviews=["小结1"],
        records=[],
        skill_content="skill stub",
        template=None,
    )
    sys_content = msgs[0]["content"]
    # template=None 时保留"6 节"原措辞
    assert "6 节" in sys_content


def test_build_synthesis_messages_template_system_no_6_sections():
    """A项: flat 路径 template 给定时，system 不应含"6 节"字样。"""
    template = get_template("master")
    msgs = synthesis._build_synthesis_messages(
        topic="测试主题",
        summaries=[],
        skill_content="skill stub",
        template=template,
    )
    sys_content = msgs[0]["content"]
    # flat 路径 system 本身已无"6 节"，但仍断言（防止回归）
    assert "6 节" not in sys_content


def test_build_meta_synthesis_messages_template_system_contains_chapter_titles():
    """A项: template 给定时，meta system 应含模板章节标题（大纲是唯一结构来源）。"""
    template = get_template("grant")
    msgs = synthesis._build_meta_synthesis_messages(
        topic="测试主题",
        group_mini_reviews=["小结1"],
        records=[],
        skill_content="skill stub",
        template=template,
    )
    sys_content = msgs[0]["content"]
    # 基金模板章节标题
    assert "研究背景" in sys_content or "基金" in sys_content or "国内外研究现状" in sys_content
