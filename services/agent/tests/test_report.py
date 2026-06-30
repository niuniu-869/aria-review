import zipfile

import pytest

from app import report as report_mod
from app.main import app


def _mk(client):
    return client.post("/projects/p/corpus",
                       files={"file": ("x.txt", b"c")},
                       data={"dbsource": "wos"}).json()["corpusId"]


# ---- 基础格式 (A7: GET → POST) ----

def test_report_md(client):
    cid = _mk(client)
    r = client.post(f"/projects/p/corpus/{cid}/report?format=md")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "领域概览" in r.text


def test_report_html(client):
    cid = _mk(client)
    r = client.post(f"/projects/p/corpus/{cid}/report?format=html")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<h1" in r.text


def test_report_bad_format_400(client):
    cid = _mk(client)
    r = client.post(f"/projects/p/corpus/{cid}/report?format=pdf")
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


# ---- A7: 标题/作者注入 ----

def test_report_title_author_injected(client):
    cid = _mk(client)
    r = client.post(
        f"/projects/p/corpus/{cid}/report?format=md",
        json={"title": "我的综述", "author": "张三", "sections": ["overview"]},
    )
    assert r.status_code == 200
    assert "# 我的综述" in r.text
    assert "张三" in r.text


# ---- A7: sections 过滤 ----

def test_report_sections_filter(client):
    cid = _mk(client)
    # 仅选 sources, 不应出现「领域概览」「核心作者」
    r = client.post(
        f"/projects/p/corpus/{cid}/report?format=md",
        json={"sections": ["sources"]},
    )
    assert r.status_code == 200
    assert "核心期刊" in r.text
    assert "领域概览" not in r.text
    assert "核心作者" not in r.text


def test_report_empty_sections_422(client):
    # 显式空 sections 应被 schema 拒绝(min_length=1), 不静默回退默认全章节 (codex A7 P2)。
    cid = _mk(client)
    r = client.post(
        f"/projects/p/corpus/{cid}/report?format=md",
        json={"sections": []},
    )
    assert r.status_code == 422


def test_report_prisma_section_with_counts(client):
    cid = _mk(client)
    r = client.post(
        f"/projects/p/corpus/{cid}/report?format=md",
        json={"sections": ["prisma"],
              "prismaCounts": {"identified": 100, "duplicates": 10,
                               "screened": 90, "excluded": 40, "included": 50}},
    )
    assert r.status_code == 200
    assert "PRISMA 流程" in r.text
    assert "纳入数: 50" in r.text


def test_report_prisma_section_without_counts_shows_placeholder(client):
    cid = _mk(client)
    r = client.post(
        f"/projects/p/corpus/{cid}/report?format=md",
        json={"sections": ["prisma"]},
    )
    assert r.status_code == 200
    assert "未提供 PRISMA" in r.text


def test_report_review_section_with_markdown(client):
    cid = _mk(client)
    r = client.post(
        f"/projects/p/corpus/{cid}/report?format=md",
        json={"sections": ["review"], "reviewMarkdown": "## 这是 AI 综述正文"},
    )
    assert r.status_code == 200
    assert "这是 AI 综述正文" in r.text


def test_report_references_uses_cite(client):
    cid = _mk(client)
    r = client.post(
        f"/projects/p/corpus/{cid}/report?format=md",
        json={"sections": ["references"]},
    )
    assert r.status_code == 200
    assert "参考文献" in r.text
    # FakeR.get_cite 返回 1 条 APA 引用
    assert "Bibliometrix" in r.text


# ---- A7: DOCX 路径 (mock pandoc 可用与不可用两路) ----

def test_report_docx_unavailable_503(client, monkeypatch):
    cid = _mk(client)
    monkeypatch.setattr(app.state, "pandoc_ok", False)
    r = client.post(f"/projects/p/corpus/{cid}/report?format=docx")
    assert r.status_code == 503
    assert r.json()["code"] == "PANDOC_UNAVAILABLE"


def test_report_docx_ok_when_pandoc_available(client, monkeypatch):
    """mock build_report 的 docx 分支返回固定二进制, 验证 200 + 正确 Content-Type。"""
    cid = _mk(client)
    monkeypatch.setattr(app.state, "pandoc_ok", True)
    fake_docx = b"PK\x03\x04fake-docx-bytes"

    def _fake_build(fmt, meta, ov, so, au, do, sections=None, pandoc_path="pandoc"):
        assert fmt == "docx"
        return fake_docx, report_mod.DOCX_MEDIA

    monkeypatch.setattr("app.main.build_report", _fake_build)
    r = client.post(f"/projects/p/corpus/{cid}/report?format=docx")
    assert r.status_code == 200
    assert r.headers["content-type"] == report_mod.DOCX_MEDIA
    assert "report.docx" in r.headers["content-disposition"]
    assert r.content == fake_docx


def test_report_docx_pandoc_timeout_503(client, monkeypatch):
    cid = _mk(client)
    monkeypatch.setattr(app.state, "pandoc_ok", True)

    def _raise(*a, **k):
        raise report_mod.PandocTimeout("超时")

    monkeypatch.setattr("app.main.build_report", _raise)
    r = client.post(f"/projects/p/corpus/{cid}/report?format=docx")
    assert r.status_code == 503
    assert r.json()["code"] == "PANDOC_TIMEOUT"


def test_report_docx_pandoc_failed_500(client, monkeypatch):
    cid = _mk(client)
    monkeypatch.setattr(app.state, "pandoc_ok", True)

    def _raise(*a, **k):
        raise report_mod.PandocFailed("失败")

    monkeypatch.setattr("app.main.build_report", _raise)
    r = client.post(f"/projects/p/corpus/{cid}/report?format=docx")
    assert r.status_code == 500
    assert r.json()["code"] == "PANDOC_FAILED"


# ---- report.py 单元: 真跑 pandoc md→docx (pandoc 3.1.3 已装) ----

@pytest.mark.skipif(not report_mod.probe_pandoc(), reason="pandoc 不可用")
def test_to_docx_real_pandoc_produces_valid_docx():
    md = "# 标题\n\n正文一段。\n\n- 项目1\n- 项目2\n"
    data = report_mod._to_docx(md)
    # 非空 + docx (zip) 魔数 PK
    assert data[:2] == b"PK"
    assert len(data) > 0
    # docx 本质是 zip, 应能被 zipfile 打开
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "word/document.xml" in zf.namelist()


def test_build_report_docx_calls_pandoc_path(monkeypatch):
    """build_report docx 分支应复用 _md → _to_docx。"""
    called = {}

    def _fake_to_docx(md_content, pandoc_path="pandoc"):
        called["md"] = md_content
        return b"PK-bytes"

    monkeypatch.setattr(report_mod, "_to_docx", _fake_to_docx)
    content, media = report_mod.build_report(
        "docx", {"title": "T"},
        {"stats": {"documents": 1}}, {"topSources": []}, {"topAuthors": []},
        {"keywords": [], "topCited": []}, sections=["overview"],
    )
    assert content == b"PK-bytes"
    assert media == report_mod.DOCX_MEDIA
    assert "领域概览" in called["md"]


def test_to_docx_unavailable_raises(monkeypatch):
    """pandoc 二进制缺失 (FileNotFoundError) → PandocUnavailable。"""
    import subprocess as sp

    def _raise(*a, **k):
        raise FileNotFoundError("pandoc")

    monkeypatch.setattr(sp, "run", _raise)
    with pytest.raises(report_mod.PandocUnavailable):
        report_mod._to_docx("# x", pandoc_path="definitely-not-pandoc-xyz")


def test_to_docx_timeout_raises(monkeypatch):
    import subprocess as sp

    def _raise(*a, **k):
        raise sp.TimeoutExpired(cmd="pandoc", timeout=30)

    monkeypatch.setattr(sp, "run", _raise)
    with pytest.raises(report_mod.PandocTimeout):
        report_mod._to_docx("# x")


def test_normalize_sections_dedup_and_default():
    assert report_mod._normalize_sections([]) == report_mod.DEFAULT_SECTIONS
    assert report_mod._normalize_sections(None) == report_mod.DEFAULT_SECTIONS
    # 去重保序 + 过滤非法
    assert report_mod._normalize_sections(
        ["sources", "sources", "bogus", "overview"]
    ) == ["sources", "overview"]


# ---- 综述导出 anchor 泄漏 + 编码(BOM) 修复回归 ----
def test_strip_export_anchors_keeps_citation_number():
    s = "盈余[[anchor:a582_5_0__occ0]][11][[/anchor]]管理"
    assert report_mod._strip_export_anchors(s) == "盈余[11]管理"


def test_md_export_strips_anchors_and_prepends_utf8_bom():
    meta = {"title": "T", "reviewMarkdown": "X[[anchor:a1_2_0__occ0]][3][[/anchor]]Y"}
    content, media = report_mod.build_report(
        "md", meta, {"stats": {}}, {}, {}, {}, ["review"])
    assert content.startswith("\ufeff")              # UTF-8 BOM
    assert "[[anchor" not in content                  # 锚点剥离
    assert "[3]" in content                           # 保留 [n] 引用编号
