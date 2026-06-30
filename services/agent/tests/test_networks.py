_MISSING = "99999999-9999-4999-8999-999999999999"


def _mk(client):
    return client.post("/projects/p/corpus",
                       files={"file": ("x.txt", b"c")},
                       data={"dbsource": "wos"}).json()["corpusId"]


def test_conceptual_ok(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/conceptual")
    assert r.status_code == 200
    b = r.json()
    assert b["projectId"] == "p"
    assert b["network"] == "co-occurrence-keywords"
    assert len(b["graph"]["nodes"]) == 2
    assert len(b["graph"]["edges"]) == 1


def test_intellectual_ok(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/intellectual")
    assert r.status_code == 200
    assert r.json()["network"] == "co-citation-references"


def test_social_ok(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/social")
    assert r.status_code == 200
    b = r.json()
    assert "authorCollab" in b and "countryCollab" in b
    assert len(b["authorCollab"]["nodes"]) == 2


def test_conceptual_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/conceptual")
    assert r.status_code == 404
