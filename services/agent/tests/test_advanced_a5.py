"""A5 高级图② — 四个信封端点透传三态 + 网络端点 limit 透传。

策略: 复用 conftest 的 FakeR(client fixture)。FakeR 默认:
  - conceptual/thematic   → available:true (12 聚类的形状缩样)
  - conceptual/evolution  → available:false / not_enough_data (模拟年份不足降级)
  - intellectual/histcite → available:true
  - overview/threefield   → available:true (三层 layer 0/1/2)
覆盖 envelope 三态 (true / false-降级仍 HTTP 200 / 语料不存在 404)。
另断言网络端点 ?limit 透传给 RClient (默认 100, 上限 100)。
"""
_MISSING = "99999999-9999-4999-8999-999999999999"


def _mk(client):
    return client.post("/projects/p/corpus",
                       files={"file": ("x.txt", b"c")},
                       data={"dbsource": "wos"}).json()["corpusId"]


# ---------------- 信封端点: available:true 透传 ----------------

def test_thematic_available_true(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/conceptual/thematic")
    assert r.status_code == 200
    b = r.json()
    assert b["available"] is True
    assert b["projectId"] == "p"
    assert b["corpusId"] == cid
    c0 = b["data"]["clusters"][0]
    assert c0["label"] == "textual analysis"
    assert c0["centrality"] == 12.0 and c0["density"] == 11.0 and c0["freq"] == 47


def test_histcite_available_true(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/intellectual/histcite")
    assert r.status_code == 200
    b = r.json()
    assert b["available"] is True
    assert len(b["data"]["nodes"]) == 2
    assert b["data"]["nodes"][0]["id"] == "1"
    assert b["data"]["nodes"][0]["localCites"] == 29
    # 边以 from/to 序列化 (alias)
    assert b["data"]["edges"][0]["from"] == "2"
    assert b["data"]["edges"][0]["to"] == "1"


def test_threefield_available_true(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/overview/threefield")
    assert r.status_code == 200
    b = r.json()
    assert b["available"] is True
    layers = sorted(n["layer"] for n in b["data"]["nodes"])
    assert layers == [0, 1, 2]
    assert b["data"]["links"][0]["value"] == 3


# ---------------- 信封端点: available:false 降级 (仍 HTTP 200) ----------------

def test_evolution_not_enough_data_is_200(client):
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/conceptual/evolution")
    # 关键: available:false 也是 HTTP 200, 非 502
    assert r.status_code == 200
    b = r.json()
    assert b["available"] is False
    assert b["reason"] == "not_enough_data"
    assert "message" in b
    assert b["projectId"] == "p"
    # 判别式联合: false 分支无 data, 不应被 response_model 拒绝
    assert "data" not in b or b.get("data") is None


# ---------------- 信封端点: 语料不存在 → 404 (非信封) ----------------

def test_thematic_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/conceptual/thematic")
    assert r.status_code == 404
    assert r.json()["code"] == "CORPUS_NOT_FOUND"


def test_evolution_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/conceptual/evolution")
    assert r.status_code == 404


def test_histcite_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/intellectual/histcite")
    assert r.status_code == 404


def test_threefield_404(client):
    r = client.get(f"/projects/p/corpus/{_MISSING}/overview/threefield")
    assert r.status_code == 404


# ---------------- 网络端点 limit 透传 (A5 §4.4) ----------------

def test_conceptual_limit_passthrough(client, fake_r):
    """前端请求 ?limit=100 → 透传给 RClient.get_conceptual。"""
    seen = {}

    async def _patched(corpus_id, limit=100):
        seen["limit"] = limit
        if corpus_id not in fake_r.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "network": "co-occurrence-keywords", "graph": fake_r._GRAPH}

    fake_r.get_conceptual = _patched
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/conceptual?limit=100")
    assert r.status_code == 200
    assert seen["limit"] == 100


def test_conceptual_limit_default_100(client, fake_r):
    """未传 limit → 默认 100。"""
    seen = {}

    async def _patched(corpus_id, limit=100):
        seen["limit"] = limit
        if corpus_id not in fake_r.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "network": "co-occurrence-keywords", "graph": fake_r._GRAPH}

    fake_r.get_conceptual = _patched
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/conceptual")
    assert r.status_code == 200
    assert seen["limit"] == 100


def test_social_limit_passthrough(client, fake_r):
    seen = {}

    async def _patched(corpus_id, limit=100):
        seen["limit"] = limit
        if corpus_id not in fake_r.corpora:
            return 404, {"code": "CORPUS_NOT_FOUND", "message": "语料不存在"}
        return 200, {"schemaVersion": 1, "corpusId": corpus_id,
                     "authorCollab": fake_r._GRAPH, "countryCollab": fake_r._GRAPH}

    fake_r.get_social = _patched
    cid = _mk(client)
    r = client.get(f"/projects/p/corpus/{cid}/social?limit=50")
    assert r.status_code == 200
    assert seen["limit"] == 50


def test_a5_and_network_null_items_return_200(client, fake_r):
    """A5 信封及网络 DTO 的 R 数据叶子为 null 时应保持 HTTP 200。"""
    cid = _mk(client)

    async def thematic_with_null(_corpus_id):
        return 200, {
            "available": True, "schemaVersion": 1, "corpusId": cid,
            "data": {"clusters": [{"label": None, "centrality": None,
                                     "density": None, "freq": None}]},
        }

    async def conceptual_with_null(_corpus_id, limit=100):
        return 200, {
            "schemaVersion": 1, "corpusId": cid, "network": None,
            "graph": {
                "nodes": [{"id": None, "label": None, "value": None}],
                "edges": [{"source": None, "target": None, "weight": None}],
            },
        }

    fake_r.get_thematic = thematic_with_null
    fake_r.get_conceptual = conceptual_with_null

    thematic = client.get(f"/projects/p/corpus/{cid}/conceptual/thematic")
    conceptual = client.get(f"/projects/p/corpus/{cid}/conceptual")

    assert thematic.status_code == 200
    assert thematic.json()["data"]["clusters"][0]["label"] is None
    assert conceptual.status_code == 200
    assert conceptual.json()["graph"]["nodes"][0]["id"] is None
