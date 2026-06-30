"""路径 A/B 数据接入端点 (from-topic / from-refs) 测试。"""
import app.main as main_mod
from app.refs import _extract_json_obj, _norm_paper, extract_papers


# ---- from-topic (路径 A) ----

def test_from_topic_happy(client):
    r = client.post("/projects/p/corpus/from-topic", json={"query": "ipo textual", "n": 8})
    assert r.status_code == 202
    b = r.json()
    assert b["projectId"] == "p"
    assert b["status"] == "ready"
    assert b["corpusId"]
    assert b["documentCount"] == 8


def test_from_topic_no_results_422(client):
    r = client.post("/projects/p/corpus/from-topic", json={"query": "__noresults__"})
    assert r.status_code == 422
    assert r.json()["code"] == "NO_RESULTS"


def test_from_topic_empty_query_422(client):
    r = client.post("/projects/p/corpus/from-topic", json={"query": ""})
    assert r.status_code == 422  # pydantic min_length


def test_from_topic_n_over_limit_422(client):
    r = client.post("/projects/p/corpus/from-topic", json={"query": "x", "n": 500})
    assert r.status_code == 422  # pydantic le=200


def test_from_topic_bad_since_falls_back(client):
    # 非法 since 被替换为默认, 不应报错
    r = client.post("/projects/p/corpus/from-topic",
                    json={"query": "x", "n": 5, "since": "not-a-date; DROP"})
    assert r.status_code == 202


# ---- from-refs (路径 B) ----

def test_from_refs_no_papers_422(client):
    # 无 LLM key → FakeStreamClient 抽不出 JSON → 空 papers → NO_PAPERS
    r = client.post("/projects/p/corpus/from-refs", json={"text": "Smith J (2020) something"})
    assert r.status_code == 422
    assert r.json()["code"] == "NO_PAPERS"


def test_from_refs_happy(client, monkeypatch):
    async def fake_extract(llm, text, max_papers=80):
        return [{"title": "A study of IPO", "doi": "10.1/x", "year": 2020, "authors": ["Smith J"]}]
    monkeypatch.setattr(main_mod, "extract_papers", fake_extract)
    r = client.post("/projects/p/corpus/from-refs", json={"text": "Smith J (2020) A study of IPO"})
    assert r.status_code == 202
    b = r.json()
    assert b["status"] == "ready"
    assert b["matched"] == 1
    assert b["extracted"] == 1
    assert b["unmatched"] == 0


def test_from_refs_empty_text_422(client):
    r = client.post("/projects/p/corpus/from-refs", json={"text": ""})
    assert r.status_code == 422  # pydantic min_length


# ---- refs 解析纯函数 ----

def test_extract_json_tolerates_fences():
    obj = _extract_json_obj('```json\n{"papers": [{"title": "x"}]}\n```')
    assert obj["papers"][0]["title"] == "x"
    assert _extract_json_obj("garbage no json") == {}


def test_norm_paper_validates_title_and_types():
    assert _norm_paper({"title": "  T  ", "year": 2020, "doi": "10.1/x"}) == {
        "title": "T", "doi": "10.1/x", "year": 2020, "authors": []}
    assert _norm_paper({"title": ""}) is None          # 空标题剔除
    assert _norm_paper({"authors": ["A"]}) is None       # 无标题剔除
    # year 非法 / authors 非 list → 安全降级
    out = _norm_paper({"title": "T", "year": "abc", "authors": "X"})
    assert out["year"] is None and out["authors"] == []
