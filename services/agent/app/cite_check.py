"""AI 输出引用完整性校验 (移植自 legacy fct_cite_check.R)。

提取 AI markdown 中的引用 (DOI/PMID/作者+年/编号), 逐条比对语料 records, 三色判定:
  green ✅ DOI/PMID 精确命中 | yellow ⚠️ 作者+年模糊命中或编号待核 | red ❌ 疑似虚构。
纯函数, 无副作用, 便于单测。records 为 r-analysis /records 返回的列表
(每条 {idx,title,authors,year,doi}); authors 形如 "ARIA M;CUCCURULLO C"。
"""
from __future__ import annotations

import re

CITE_MARK = {"green": "✅", "yellow": "⚠️", "red": "❌"}

_CJK = r"一-鿿"
_LAT = r"A-Za-zÀ-ɏ'’\-"

_PATTERNS = {
    "doi": re.compile(r"10\.\d{4,}/[^\s,;)\]。，）]+"),
    "pmid": re.compile(r"PMID[:\s]+\d+"),
    "cn": re.compile(
        rf"[{_CJK}]{{2,4}}(?:等)?\s*[（(]\s*\d{{4}}\s*[）)]"
        rf"|[（(]\s*[{_CJK}]{{2,4}}\s*[,，]\s*\d{{4}}\s*[）)]"
    ),
    "en": re.compile(
        rf"[A-Z][{_LAT}]+(?:\s+et al\.?|\s+(?:and|&)\s+[A-Z][{_LAT}]+)?\s*\(\s*\d{{4}}[a-z]?\s*\)"
        rf"|\(\s*[A-Z][{_LAT}]+(?:\s+et al\.?|\s+(?:and|&)\s+[A-Z][{_LAT}]+)?\s*,\s*\d{{4}}[a-z]?\s*\)"
    ),
    "num": re.compile(r"\[\d{1,3}\]"),
}


def _norm_doi(x: str) -> str:
    x = (x or "").strip().lower()
    x = re.sub(r"^https?://(dx\.)?doi\.org/", "", x)
    x = re.sub(r"^doi:\s*", "", x)
    return re.sub(r"[.,;:)\]。，]+$", "", x)


def _norm_name(x: str) -> str:
    return re.sub(rf"[^a-z{_CJK}]", "", (x or "").lower())


def _build_index(records: list[dict]) -> dict:
    doi, pmid, title, surnames, year = [], [], [], [], []
    for r in records or []:
        doi.append(_norm_doi(str(r.get("doi", "") or "")))
        pmid.append(str(r.get("pmid", "") or "").strip())
        title.append(str(r.get("title", "") or "").strip().lower())
        y = r.get("year")
        try:
            year.append(int(y))
        except (TypeError, ValueError):
            year.append(None)
        au = str(r.get("authors", "") or "")
        fams = set()
        for a in au.split(";"):
            a = a.strip()
            if not a:
                continue
            toks = [t for t in re.split(r"[ ,]+", a) if t]
            if toks:
                nm = _norm_name(toks[0])
                if nm:
                    fams.add(nm)
        surnames.append(fams)
    return {"doi": doi, "pmid": pmid, "title": title, "surnames": surnames,
            "year": year, "n": len(records or [])}


def _extract(ai_text: str) -> list[dict]:
    if not ai_text:
        return []
    hits = []
    for ty, pat in _PATTERNS.items():
        for m in pat.finditer(ai_text):
            hits.append({"text": m.group(0), "type": ty, "start": m.start(), "end": m.end() - 1})
    if not hits:
        return []
    # 去重叠: start 升序, 长度降序; 保留不重叠 (强信号 DOI/PMID 因更长优先)
    hits.sort(key=lambda h: (h["start"], -(h["end"] - h["start"])))
    kept, last_end = [], -1
    for h in hits:
        if h["start"] <= last_end:
            continue
        kept.append(h)
        last_end = h["end"]
    return kept


def _parse_author_year(text: str, type_: str) -> tuple[str, int | None]:
    ym = re.search(r"\d{4}", text)
    year = int(ym.group(0)) if ym else None
    if type_ == "cn":
        sm = re.search(rf"[{_CJK}]{{2,4}}", text)
    else:
        sm = re.search(rf"[{_LAT}]{{2,}}", text)
    sur = sm.group(0) if sm else ""
    sur = re.sub(r"等$", "", sur)
    return _norm_name(sur), year


def _judge(text: str, type_: str, idx: dict) -> tuple[str, int | None]:
    if idx["n"] == 0:
        return "red", None
    if type_ == "doi":
        key = _norm_doi(text)
        for i, d in enumerate(idx["doi"]):
            if d and d == key:
                return "green", i + 1
        return "red", None
    if type_ == "pmid":
        key = re.sub(r"^PMID[:\s]+", "", text).strip()
        for i, p in enumerate(idx["pmid"]):
            if p and p == key:
                return "green", i + 1
        return "red", None
    if type_ in ("en", "cn"):
        surname, year = _parse_author_year(text, type_)
        if not surname:
            return "red", None
        # "英文姓 等人（年）" 这类混排里, CJK 正则只抓到 et-al 标记「等人/等/人」当姓名
        # (英文姓在 Latin 段未被捕获)。这是解析局限, 不是伪造 → 判 yellow(待核), 不标红。
        # 否则综述里每个"X 等人(年)"都被误标 ❌, 与"零伪造"严重矛盾 (接手 dogfood 实测命门)。
        if surname in ("等", "等人", "人", "et", "al", "etal"):
            return "yellow", None
        cand = [i for i in range(idx["n"]) if surname in idx["surnames"][i]]
        if not cand:
            return "red", None
        if year is not None:
            both = [i for i in cand if idx["year"][i] is not None and idx["year"][i] == year]
            if both:
                return "yellow", both[0] + 1
            # 作者命中但年份明确不符 → 疑似伪造年份 (Codex slice2-P1)
            return "red", None
        return "yellow", cand[0] + 1  # 仅姓命中, 无年份可校验
    if type_ == "num":
        # 编号 [n] 指向 top_docs 行号: 范围内=命中真实文献, 越界=疑似虚构 (Codex slice2-P1)
        m = re.search(r"\d+", text)
        n = int(m.group(0)) if m else 0
        if 1 <= n <= idx["n"]:
            return "green", n
        return "red", None
    return "red", None


def _annotate(ai_text: str, cites: list[dict]) -> str:
    if not cites:
        return ai_text
    out = ai_text
    for c in sorted(cites, key=lambda x: x["end"], reverse=True):
        mk = CITE_MARK[c["status"]]
        en = c["end"]
        out = out[: en + 1] + " " + mk + out[en + 1:]
    return out


def check_citations(ai_text: str, records: list[dict]) -> dict:
    """校验 AI 输出引用。返回 {cites, annotated, summary}。"""
    empty = {"cites": [], "annotated": ai_text or "", "summary": {"green": 0, "yellow": 0, "red": 0}}
    if not ai_text or not ai_text.strip():
        return {"cites": [], "annotated": "", "summary": {"green": 0, "yellow": 0, "red": 0}}

    idx = _build_index(records)
    raw = _extract(ai_text)
    if not raw:
        return empty

    for c in raw:
        status, matched = _judge(c["text"], c["type"], idx)
        c["status"] = status
        c["matched_idx"] = matched

    annotated = _annotate(ai_text, raw)
    cites = [{"text": c["text"], "type": c["type"], "status": c["status"],
              "matched_idx": c["matched_idx"]} for c in raw]
    summary = {
        "green": sum(1 for c in cites if c["status"] == "green"),
        "yellow": sum(1 for c in cites if c["status"] == "yellow"),
        "red": sum(1 for c in cites if c["status"] == "red"),
    }
    return {"cites": cites, "annotated": annotated, "summary": summary}
