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


def test_sources_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/sources")
    assert r.status_code == 404
    assert r.json()["code"] == "CORPUS_NOT_FOUND"
