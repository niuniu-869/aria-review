from app.errors import ApiError

_MISSING = "99999999-9999-4999-8999-999999999999"


def test_upload_returns_202_with_projectId(client):
    r = client.post("/projects/proj1/corpus",
                    files={"file": ("x.txt", b"some wos content")},
                    data={"dbsource": "wos"})
    assert r.status_code == 202
    b = r.json()
    assert b["projectId"] == "proj1"
    assert b["status"] == "ready"
    assert b["corpusId"]
    assert b["schemaVersion"] == 1


def test_upload_parse_failed_still_202_with_failed_status(client):
    r = client.post("/projects/p/corpus",
                    files={"file": ("x.txt", b"BAD content")},
                    data={"dbsource": "wos"})
    assert r.status_code == 202
    assert r.json()["status"] == "failed"


def test_upload_bad_dbsource_400(client):
    r = client.post("/projects/p/corpus",
                    files={"file": ("x.txt", b"c")},
                    data={"dbsource": "foo"})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_upload_empty_file_400(client):
    r = client.post("/projects/p/corpus",
                    files={"file": ("x.txt", b"")},
                    data={"dbsource": "wos"})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_overview_happy(client):
    up = client.post("/projects/p/corpus",
                     files={"file": ("x.txt", b"c")}, data={"dbsource": "wos"})
    cid = up.json()["corpusId"]
    r = client.get(f"/projects/p/corpus/{cid}/overview")
    assert r.status_code == 200
    b = r.json()
    assert b["projectId"] == "p"
    assert b["corpusId"] == cid
    assert b["stats"]["documents"] == 3
    assert len(b["annualProduction"]) == 2


def test_overview_not_found_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/overview")
    assert r.status_code == 404
    assert r.json()["code"] == "CORPUS_NOT_FOUND"


def test_corpus_status_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}")
    assert r.status_code == 404
    assert r.json()["code"] == "CORPUS_NOT_FOUND"


def test_corpus_status_happy(client):
    up = client.post("/projects/p/corpus",
                     files={"file": ("x.txt", b"c")}, data={"dbsource": "wos"})
    cid = up.json()["corpusId"]
    r = client.get(f"/projects/p/corpus/{cid}")
    assert r.status_code == 200
    assert r.json()["projectId"] == "p"
    assert r.json()["status"] == "ready"


def test_r_unavailable_maps_503(client, fake_r):
    async def boom(corpus_id):
        raise ApiError(503, "R_SERVICE_UNAVAILABLE", "R 分析服务不可达")
    fake_r.get_overview = boom
    r = client.get(f"/projects/p/corpus/{_MISSING}/overview")
    assert r.status_code == 503
    assert r.json()["code"] == "R_SERVICE_UNAVAILABLE"


def test_overview_not_ready_maps_409(client, fake_r):
    async def parsing(corpus_id):
        return 409, {"code": "CORPUS_NOT_READY", "message": "解析中"}
    fake_r.get_overview = parsing
    r = client.get(f"/projects/p/corpus/{_MISSING}/overview")
    assert r.status_code == 409
    assert r.json()["code"] == "CORPUS_NOT_READY"


def test_overview_parse_failed_maps_422(client, fake_r):
    async def failed(corpus_id):
        return 422, {"code": "PARSE_FAILED", "message": "解析失败"}
    fake_r.get_overview = failed
    r = client.get(f"/projects/p/corpus/{_MISSING}/overview")
    assert r.status_code == 422
    assert r.json()["code"] == "PARSE_FAILED"


def test_get_corpus_propagates_409(client, fake_r):
    async def parsing(corpus_id):
        return 409, {"code": "CORPUS_NOT_READY", "message": "解析中"}
    fake_r.get_corpus = parsing
    r = client.get(f"/projects/p/corpus/{_MISSING}")
    assert r.status_code == 409


def test_upload_too_large_propagates_413(client, fake_r):
    async def too_big(content, filename, dbsource):
        return 413, {"code": "PAYLOAD_TOO_LARGE", "message": "太大"}
    fake_r.parse = too_big
    r = client.post("/projects/p/corpus",
                    files={"file": ("x.txt", b"c")}, data={"dbsource": "wos"})
    assert r.status_code == 413
    assert r.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_request_id_injection_sanitized(client):
    r = client.get("/healthz", headers={"X-Request-ID": "bad\r\nInjected: 1"})
    headers = {k.lower(): v for k, v in r.headers.items()}
    # 含控制字符的值被替换为生成的 uuid (不原样回写)
    assert headers["x-request-id"] != "bad\r\nInjected: 1"
    assert "\n" not in headers["x-request-id"]
