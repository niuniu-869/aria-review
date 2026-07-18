_MISSING = "99999999-9999-4999-8999-999999999999"


def _mk(client):
    return client.post("/projects/p/corpus",
                       files={"file": ("x.txt", b"c")},
                       data={"dbsource": "wos"}).json()["corpusId"]


def test_sources_ok(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/sources")
    assert r.status_code == 200
    b = r.json()
    assert b["projectId"] == "p"
    assert b["corpusId"] == cid
    assert b["topSources"][0]["source"]
    assert "hIndex" in b and "bradford" in b


def test_authors_ok(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/authors")
    assert r.status_code == 200
    b = r.json()
    assert b["projectId"] == "p"
    assert b["topAuthors"][0]["author"]
    assert b["lotka"]["beta"] == 2.1


def test_documents_ok(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/documents")
    assert r.status_code == 200
    b = r.json()
    assert b["topCited"][0]["cited"] == 99
    assert b["keywords"][0]["term"] == "bibliometrics"


def test_r_analysis_null_items_return_200(client, fake_r):
    """R 数据叶子为 null 时应原样返回，而不是触发 ResponseValidationError。"""
    cid = _mk(client)

    async def sources_with_null(_corpus_id):
        return 200, {
            "schemaVersion": 1,
            "corpusId": cid,
            "topSources": [{"source": None, "articles": None}],
            "hIndex": [{"source": None, "h": None}],
            "bradford": [{"source": None, "zone": None, "freq": None}],
        }

    async def authors_with_null(_corpus_id):
        return 200, {
            "schemaVersion": 1,
            "corpusId": cid,
            "topAuthors": [{"author": None, "articles": None}],
            "hIndex": [{"author": None, "h": None}],
            "lotka": {"beta": None, "distribution": [{"articles": None, "authors": None}]},
        }

    async def documents_with_null(_corpus_id):
        return 200, {
            "schemaVersion": 1,
            "corpusId": cid,
            "topCited": [{"title": None, "author": None, "year": None, "cited": None}],
            "keywords": [{"term": None, "freq": None}],
        }

    fake_r.get_sources = sources_with_null
    fake_r.get_authors = authors_with_null
    fake_r.get_documents = documents_with_null

    sources = client.get(f"/projects/p/corpus/{cid}/sources")
    authors = client.get(f"/projects/p/corpus/{cid}/authors")
    documents = client.get(f"/projects/p/corpus/{cid}/documents")

    assert sources.status_code == 200
    assert sources.json()["hIndex"][0]["source"] is None
    assert authors.status_code == 200
    assert authors.json()["topAuthors"][0]["author"] is None
    assert documents.status_code == 200
    assert documents.json()["topCited"][0]["title"] is None


def test_sources_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/sources")
    assert r.status_code == 404
    assert r.json()["code"] == "CORPUS_NOT_FOUND"
