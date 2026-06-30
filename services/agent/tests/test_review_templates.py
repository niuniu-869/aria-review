"""Task 3 — 6 论型综述模板移植测试

TDD: 先跑失败（templates.py 不存在） → 实现 → 跑通过。
"""
from app.review.templates import get_template, PAPER_TYPE_TEMPLATES, REVIEW_GROUNDING_DIRECTIVE


def test_six_templates_exist():
    assert set(PAPER_TYPE_TEMPLATES) == {"undergrad", "master", "phd", "grant", "proposal", "sci_intro"}


def test_phd_template_structure():
    t = get_template("phd")
    assert t.name == "博士论文综述"
    assert len(t.chapters) == 5
    assert t.chapters[0].title == "研究背景与理论基础"
    assert t.chapters[0].word_budget == 1200
    assert "抗幻觉" in REVIEW_GROUNDING_DIRECTIVE


def test_unknown_type_returns_none():
    assert get_template("xxx") is None


def test_none_type_returns_none():
    assert get_template(None) is None


# --- 逐论型结构验证 ---

def test_undergrad_template():
    t = get_template("undergrad")
    assert t is not None
    assert t.name == "本科毕业论文综述"
    assert t.tone == "规范"
    assert len(t.chapters) == 3
    assert t.chapters[0].title == "研究背景与意义"
    assert t.chapters[0].word_budget == 600
    assert t.chapters[1].title == "国内外研究现状"
    assert t.chapters[1].word_budget == 1200
    assert t.chapters[2].title == "研究述评与展望"
    assert t.chapters[2].word_budget == 600


def test_master_template():
    t = get_template("master")
    assert t is not None
    assert t.name == "硕士论文综述"
    assert t.tone == "学术"
    assert len(t.chapters) == 4
    assert t.chapters[0].title == "研究背景与问题"
    assert t.chapters[0].word_budget == 800
    assert t.chapters[1].title == "国外研究综述"
    assert t.chapters[1].word_budget == 1500
    assert t.chapters[2].title == "国内研究综述"
    assert t.chapters[2].word_budget == 1500
    assert t.chapters[3].title == "文献述评与研究空白"
    assert t.chapters[3].word_budget == 800


def test_phd_template_full():
    t = get_template("phd")
    assert t is not None
    assert t.tone == "深入学术"
    assert len(t.chapters) == 5
    titles = [c.title for c in t.chapters]
    assert "研究背景与理论基础" in titles
    assert "主题聚类与方法学进展" in titles
    assert "研究空白与本研究定位" in titles
    # 第4章 主题聚类 word_budget = 1500
    ch4 = next(c for c in t.chapters if c.title == "主题聚类与方法学进展")
    assert ch4.word_budget == 1500


def test_grant_template():
    t = get_template("grant")
    assert t is not None
    assert t.name == "国家基金本子综述"
    assert t.tone == "精炼"
    assert len(t.chapters) == 3
    assert t.chapters[0].title == "研究意义与紧迫性"
    assert t.chapters[0].word_budget == 400
    assert t.chapters[1].title == "国内外研究进展"
    assert t.chapters[1].word_budget == 1500
    assert t.chapters[2].title == "尚需解决的关键问题"
    assert t.chapters[2].word_budget == 400


def test_proposal_template():
    t = get_template("proposal")
    assert t is not None
    assert t.name == "博士开题报告综述"
    assert t.tone == "学术"
    assert len(t.chapters) == 3
    assert t.chapters[0].title == "选题背景"
    assert t.chapters[0].word_budget == 600
    assert t.chapters[1].title == "国内外研究现状"
    assert t.chapters[1].word_budget == 1800
    assert t.chapters[2].title == "主要研究空白与本研究价值"
    assert t.chapters[2].word_budget == 600


def test_sci_intro_template():
    t = get_template("sci_intro")
    assert t is not None
    assert t.name == "SCI 论文 Introduction"
    assert t.tone == "academic English"
    assert len(t.chapters) == 3
    assert t.chapters[0].title == "Background and motivation"
    assert t.chapters[0].word_budget == 350
    assert t.chapters[1].title == "Literature gap"
    assert t.chapters[1].word_budget == 350
    assert t.chapters[2].title == "Contribution and structure"
    assert t.chapters[2].word_budget == 200


def test_template_is_frozen():
    """Template 和 Chapter 是 frozen dataclass，不可修改。"""
    import dataclasses
    t = get_template("undergrad")
    assert dataclasses.is_dataclass(t)
    try:
        t.name = "修改测试"
        assert False, "frozen dataclass 应禁止修改"
    except dataclasses.FrozenInstanceError:
        pass  # 精确捕获 frozen dataclass 修改异常


def test_grounding_directive_content():
    """抗幻觉指令包含关键约束内容。"""
    d = REVIEW_GROUNDING_DIRECTIVE
    assert "编造" in d
    assert "引用" in d
    assert "语料未覆盖" in d
