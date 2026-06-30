import pytest

from app.prisma import build_prisma


def test_build_prisma_unit():
    out = build_prisma(100, 20, 80, 50, 30)
    assert out["schemaVersion"] == 1
    assert len(out["stages"]) == 5
    assert out["stages"][0]["count"] == 100
    assert out["warnings"] == []


def test_build_prisma_negative_raises():
    with pytest.raises(ValueError):
        build_prisma(-1, 0, 0, 0, 0)


def test_prisma_endpoint_ok(client):
    r = client.post("/projects/p/prisma",
                    json={"identified": 100, "duplicates": 20, "screened": 80,
                          "excluded": 50, "included": 30})
    assert r.status_code == 200
    b = r.json()
    assert len(b["stages"]) == 5
    assert b["warnings"] == []


def test_prisma_endpoint_inconsistent_warns(client):
    r = client.post("/projects/p/prisma",
                    json={"identified": 100, "duplicates": 20, "screened": 99,
                          "excluded": 50, "included": 30})
    assert r.status_code == 200
    assert len(r.json()["warnings"]) >= 1


def test_prisma_endpoint_negative_422(client):
    r = client.post("/projects/p/prisma",
                    json={"identified": -1, "duplicates": 0, "screened": 0,
                          "excluded": 0, "included": 0})
    assert r.status_code == 422
