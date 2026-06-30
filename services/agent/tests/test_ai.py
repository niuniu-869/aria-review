_MISSING = "99999999-9999-4999-8999-999999999999"


def _mk(client):
    return client.post("/projects/p/corpus",
                       files={"file": ("x.txt", b"c")},
                       data={"dbsource": "wos"}).json()["corpusId"]


def test_translate_ok(client):
    r = client.post("/projects/p/ai/translate", json={"text": "hello world", "direction": "en2zh"})
    assert r.status_code == 200
    assert r.json()["text"]


def test_translate_bad_direction_400(client):
    r = client.post("/projects/p/ai/translate", json={"text": "x", "direction": "xx"})
    assert r.status_code == 400


def test_rewrite_ok(client):
    r = client.post("/projects/p/ai/rewrite", json={"text": "一段文字", "action": "compress"})
    assert r.status_code == 200
    assert r.json()["text"]


def test_rewrite_bad_action_400(client):
    r = client.post("/projects/p/ai/rewrite", json={"text": "x", "action": "nope"})
    assert r.status_code == 400


def test_summary_ok(client):
    r = client.post("/projects/p/ai/summary", json={"text": "这是一段摘要文本。"})
    assert r.status_code == 200
    assert r.json()["text"]


def test_screen_ok(client):
    cid = _mk(client)
    r = client.post(f"/projects/p/corpus/{cid}/ai/screen", json={"topic": "人工智能", "limit": 2})
    assert r.status_code == 200
    res = r.json()["results"]
    assert len(res) <= 2
    assert all("idx" in x and "reason" in x for x in res)


def test_chat_sse(client):
    cid = _mk(client)
    with client.stream("POST", f"/projects/p/corpus/{cid}/ai/chat",
                       json={"query": "这些文献的主题是什么?", "history": []}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: token" in body
    assert "event: done" in body


def test_chat_missing_corpus_404(client):
    r = client.post(f"/projects/p/corpus/{_MISSING}/ai/chat", json={"query": "x"})
    assert r.status_code == 404


def test_cite_ok(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/cite?style=apa")
    assert r.status_code == 200
    b = r.json()
    assert b["style"] == "apa"
    assert b["projectId"] == "p"
    assert len(b["citations"]) >= 1


def test_cite_bad_style_400(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/cite?style=bad")
    assert r.status_code == 400
