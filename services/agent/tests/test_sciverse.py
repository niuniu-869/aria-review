from starlette.datastructures import Headers
from starlette.requests import Request

import pytest

from app import main as app_main
from app.errors import ApiError
from app.schemas import (
    SciverseAgenticSearchRequest,
    SciverseFetchContentRequest,
    SciverseMetaSearchRequest,
    SciverseSettingsPayload,
)
from app.sciverse import sciverse_config, normalize_meta_result


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request({"type": "http", "headers": Headers(headers or {}).raw})


def test_normalize_meta_result_preserves_sciverse_ids():
    row = {
        "title": "Cycle stability of graphene composite cathodes",
        "doi": "10.1234/xyz",
        "doc_id": "d_2a91",
        "unique_id": "u_123",
        "author": ["Alice", "Bob"],
        "publication_published_year": 2024,
        "publication_venue_name_unified": "Adv. Energy Mater.",
        "citation_count": 42,
    }

    candidate = normalize_meta_result(row)

    assert candidate["source"] == "sciverse"
    assert candidate["provider"] == "sciverse"
    assert candidate["sciverseDocId"] == "d_2a91"
    assert candidate["sciverseUniqueId"] == "u_123"
    assert candidate["title"] == row["title"]
    assert candidate["containerTitle"] == row["publication_venue_name_unified"]

    ids = {
        (item["provider"], item["id_type"], item["external_id"])
        for item in candidate["externalIds"]
    }
    assert ("sciverse", "doc_id", "d_2a91") in ids
    assert ("sciverse", "unique_id", "u_123") in ids
    assert ("doi", "doi", "10.1234/xyz") in ids


def test_normalize_meta_result_maps_author_and_keywords_to_candidate_fields():
    row = {
        "title": "AI for smart structures",
        "author": ["Alice", "Bob"],
        "keywords": ["AI", "structural design", "AI"],
        "publication_published_year": 2025,
    }

    candidate = normalize_meta_result(row)

    assert candidate["authors"] == ["Alice", "Bob"]
    assert candidate["keywords"] == "AI; structural design"


def test_normalize_meta_result_accepts_keyword_aliases():
    row = {
        "title": "Keyword aliases",
        "author_keywords": "civil engineering, smart structure",
        "subjects": ["machine learning"],
    }

    candidate = normalize_meta_result(row)

    assert candidate["keywords"] == "civil engineering; smart structure; machine learning"


def test_sciverse_config_accepts_bearer_prefixed_token():
    cfg = sciverse_config("https://api.sciverse.space/", "Bearer token-123")

    assert cfg.base_url == "https://api.sciverse.space"
    assert cfg.api_token == "token-123"


def test_sciverse_config_rejects_private_base_url():
    with pytest.raises(ApiError) as exc:
        sciverse_config("http://169.254.169.254/latest", "token-123")
    assert exc.value.code == "SCIVERSE_BASE_URL_INVALID"


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (SciverseSettingsPayload, {"apiToken": "body-token"}),
        (SciverseMetaSearchRequest, {"query": "bibliometrics", "apiToken": "body-token"}),
        (SciverseAgenticSearchRequest, {"query": "bibliometrics", "apiToken": "body-token"}),
        (SciverseFetchContentRequest, {"docId": "doc-1", "apiToken": "body-token"}),
    ],
)
def test_sciverse_schemas_reject_body_api_token(schema, payload):
    with pytest.raises(ValueError) as exc:
        schema.model_validate(payload)

    assert "Extra inputs are not permitted" in str(exc.value)


def test_sciverse_override_reads_token_only_from_header():
    request = _request({
        "X-Sciverse-Base-URL": "https://api.sciverse.space",
        "X-Sciverse-Token": "header-token",
    })
    body = SciverseSettingsPayload(baseUrl="https://body.example.test")

    assert app_main._sciverse_override(request, body) == ("https://body.example.test", "header-token")


# ---- 引用导出乱码修复回归 ----
from app.sciverse import _authors


def test_authors_extracts_name_from_dict_not_stringified():
    # Sciverse 现返回 [{'orcid','name'}]; 旧版 str(v) 会把整个字典塞进作者名
    out = _authors({"author": [{"orcid": "x", "name": "Tatang Ary Gumanti"}]})
    assert out == ["Tatang Ary Gumanti"]
    assert "{" not in out[0] and "orcid" not in out[0]


def test_authors_dedups_variants_preferring_comma_form():
    # 同一作者全名/缩写/姓在前多写法 → 去重, 优先 'Last, First'
    out = _authors({"author": [
        {"name": "Elok Sri Utami"}, {"name": "E. S. Utami"}, {"name": "Utami, Elok Sri"},
        {"name": "Tatang Ary Gumanti"}, {"name": "Gumanti, Tatang Ary"},
    ]})
    assert out == ["Utami, Elok Sri", "Gumanti, Tatang Ary"]


def test_authors_does_not_merge_distinct_same_surname_or_initial():
    # codex P1: 不能误并不同作者
    assert _authors({"author": [{"name": "Smith J"}, {"name": "Sanders J"}]}) == ["Smith J", "Sanders J"]
    assert _authors({"author": [{"name": "John Smith"}, {"name": "Jane Smith"}]}) == ["John Smith", "Jane Smith"]
