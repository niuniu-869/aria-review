def _make_corpus(client):
    up = client.post("/projects/p/corpus",
                     files={"file": ("x.txt", b"c")}, data={"dbsource": "wos"})
    return up.json()["corpusId"]


def test_review_streams_sse_events(client):
    cid = _make_corpus(client)
    with client.stream(
        "POST", f"/projects/p/corpus/{cid}/review",
        json={"type": "undergrad", "topic": "人工智能教育"},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = "".join(resp.iter_text())
    # 关键 SSE 事件齐全 (FakeStreamClient 无 key 也能跑)
    assert "event: meta" in body
    assert "event: chapter" in body
    assert "event: token" in body
    assert "event: citations" in body
    assert "event: done" in body


def test_review_bad_type_400(client):
    cid = _make_corpus(client)
    r = client.post(f"/projects/p/corpus/{cid}/review",
                    json={"type": "bogus", "topic": "x"})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_review_missing_corpus_404(client):
    r = client.post(
        "/projects/p/corpus/99999999-9999-4999-8999-999999999999/review",
        json={"type": "undergrad", "topic": "x"},
    )
    assert r.status_code == 404


def test_review_empty_topic_422(client):
    cid = _make_corpus(client)
    # pydantic min_length=1 → 422 validation
    r = client.post(f"/projects/p/corpus/{cid}/review",
                    json={"type": "undergrad", "topic": ""})
    assert r.status_code == 422
