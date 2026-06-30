from app.cite_check import check_citations

RECORDS = [
    {"idx": 1, "title": "Bibliometric study", "authors": "ARIA M;CUCCURULLO C",
     "year": 2017, "doi": "10.1016/j.joi.2017.08.007"},
    {"idx": 2, "title": "Science mapping", "authors": "SMITH J", "year": 2020},
]


def test_doi_exact_green():
    r = check_citations("见 10.1016/j.joi.2017.08.007 的研究。", RECORDS)
    assert r["summary"]["green"] == 1
    assert r["cites"][0]["status"] == "green"
    assert "✅" in r["annotated"]


def test_author_year_yellow():
    r = check_citations("Smith (2020) 指出该方法有效。", RECORDS)
    assert any(c["status"] == "yellow" for c in r["cites"])


def test_fabricated_red():
    r = check_citations("Nonexistent (1999) 声称发现了新规律。", RECORDS)
    assert r["summary"]["red"] >= 1


def test_numbered_in_range_green():
    r = check_citations("如 [1] 所示, 该领域增长迅速。", RECORDS)  # 2 条记录, [1] 合法
    assert any(c["type"] == "num" and c["status"] == "green" and c["matched_idx"] == 1
               for c in r["cites"])


def test_numbered_out_of_range_red():
    r = check_citations("见 [999] 的论证。", RECORDS)  # 越界
    assert any(c["type"] == "num" and c["status"] == "red" for c in r["cites"])


def test_author_wrong_year_red():
    # 作者真实但年份明确不符 → 疑似伪造年份 (Codex slice2-P1)
    r = check_citations("Smith (2099) 声称...", RECORDS)
    assert any(c["type"] == "en" and c["status"] == "red" for c in r["cites"])


def test_cn_author_year():
    recs = [{"idx": 1, "title": "x", "authors": "王五 W", "year": 2021}]
    r = check_citations("王五 (2021) 的研究表明...", recs)
    assert any(c["type"] == "cn" for c in r["cites"])
    assert any(c["status"] == "yellow" for c in r["cites"])


def test_empty_text():
    r = check_citations("", RECORDS)
    assert r["summary"] == {"green": 0, "yellow": 0, "red": 0}
    assert r["cites"] == []


def test_no_corpus_all_red():
    r = check_citations("10.1234/abc 与正文。", [])
    assert r["summary"]["red"] >= 1


def test_annotated_preserves_text():
    r = check_citations("开头 10.1016/j.joi.2017.08.007 结尾。", RECORDS)
    assert "开头" in r["annotated"] and "结尾" in r["annotated"]


def test_etal_marker_not_red():
    """『英文姓 等人(年)』混排: CJK 正则只抓到 et-al 标记当姓名 → 判 yellow(待核) 不标红。

    接手 dogfood 实测命门: 否则综述里每个 'X 等人(年)' 被误标 ❌, 与零伪造矛盾。
    真实虚构(Nonexistent)仍 red, [n] 仍 green。
    """
    recs = [{"idx": 1, "title": "t", "authors": "SHELLER M", "year": 2020}]
    r = check_citations("Sheller 等人（2020）的研究[1]表明...", recs)
    assert not any(c["type"] == "cn" and c["status"] == "red" for c in r["cites"])
    assert any(c["type"] == "cn" and c["status"] == "yellow" for c in r["cites"])
