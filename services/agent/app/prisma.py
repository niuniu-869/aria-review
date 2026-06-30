"""PRISMA 2020 流程图逻辑 (移植自 legacy fct_prisma.R 的计数模型)。

纯算术 + 一致性校验, 不需 R。5 个计数: identified/duplicates/screened/excluded/included。
"""
from __future__ import annotations


def build_prisma(identified: int, duplicates: int, screened: int,
                 excluded: int, included: int) -> dict:
    vals = {"identified": identified, "duplicates": duplicates,
            "screened": screened, "excluded": excluded, "included": included}
    for k, v in vals.items():
        if type(v) is not int or v < 0:  # type() 拒绝 bool (Codex slice5-P2)
            raise ValueError(f"{k} 必须为非负整数")

    warnings: list[str] = []
    expected_screened = identified - duplicates
    if expected_screened < 0:
        warnings.append("去重数大于识别数: identified - duplicates < 0")
    if screened != expected_screened:
        warnings.append(
            f"筛选数 ({screened}) 与 识别数-去重数 ({expected_screened}) 不一致")
    if included > screened - excluded:
        warnings.append(
            f"纳入数 ({included}) 大于 筛选数-排除数 ({screened - excluded})")

    stages = [
        {"key": "identified", "label": "识别记录数", "count": identified},
        {"key": "duplicates", "label": "去重移除", "count": duplicates},
        {"key": "screened", "label": "筛选记录数", "count": screened},
        {"key": "excluded", "label": "排除记录数", "count": excluded},
        {"key": "included", "label": "纳入研究数", "count": included},
    ]
    return {"schemaVersion": 1, "stages": stages, "warnings": warnings}
