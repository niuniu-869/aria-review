"""A3 · subagent 规格表 —— skill 的权限/限额是**代码常量**，不读 SKILL.md frontmatter。

设计抉择（依据现有 prompt/permission 分离 + 铁律「结构化只信工具」）：
SKILL.md 仅承载 SOP 正文（不可信磁盘文本，不当安全策略）；tool_ids/超时/轮数/深度等
**授权与限额**集中在本 Python 表，dispatch 据此构造最小授权子 agent。仿 FS_Agent skill.yaml
（tool_ids/timeout/max_rounds/max_depth），但落到 biblio_cn 的 ToolRegistry.tool_ids 机制。

worker 铁律：tool_ids **绝不含 dispatch**（worker 无派发权），max_depth=1（被父派发即到底，
不可再下派），写权仅限其声明的写工具。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 默认子 agent 模型（与 review/read 一致）。
_DEFAULT_MODEL = "deepseek-chat"


class SubagentSpecError(ValueError):
    """未知 skill_id / spec 引用了未注册工具（fail-loud）。"""


@dataclass(frozen=True)
class SubagentSpec:
    """一个 worker skill 的授权与限额。

    Attributes:
        skill_id:        skill 名（= SKILL_MANIFEST key = skills/<name>/SKILL.md 目录名）。
        tool_ids:        最小授权工具集（经 ToolRegistry.tool_ids 暴露给子 LLM；不含 dispatch）。
        skill_timeout:   子 agent 硬超时（秒）；dispatch 取 min(父剩余, 本值)。
        max_rounds:      子 loop 最大轮数。
        model:           子 agent 模型。
        max_depth:       允许的最大派发深度（worker=1，被父派发即到底，不可再下派）。
        collect_tool_id: 结构化收集的工具集——只信这些工具的 ToolResult.data，不解析 LLM 文本。
    """
    skill_id: str
    tool_ids: tuple[str, ...]
    skill_timeout: float = 300.0
    max_rounds: int = 12
    model: str = _DEFAULT_MODEL
    max_depth: int = 1
    collect_tool_id: tuple[str, ...] = field(default_factory=tuple)


# 两个 worker skill（领域无关；SOP 在 skills/<name>/SKILL.md）。
SUBAGENT_SPECS: dict[str, SubagentSpec] = {
    "gap-finder": SubagentSpec(
        skill_id="gap-finder",
        tool_ids=("read_paper", "scratchpad"),
        skill_timeout=300.0,
        max_rounds=12,
        max_depth=1,
        collect_tool_id=("scratchpad",),
    ),
    "value-evidence": SubagentSpec(
        skill_id="value-evidence",
        tool_ids=("read_paper", "search", "submit_evidence_pack"),
        # 价值核验做**网络反向检索**(Sciverse/OpenAlex meta-search + 限流退避),
        # 比 gap-finder 的本地读更慢; 实测 240s 不足(5 轮即超时 fail-loud)。
        # 提到 360s(>gap-finder 300s), 给网络密集型工作流足够时限收敛。
        skill_timeout=360.0,
        max_rounds=10,
        max_depth=1,
        collect_tool_id=("submit_evidence_pack",),
    ),
}


def get_spec(skill_id: str) -> SubagentSpec:
    """取 skill 规格；未知即 fail-loud（绝不静默回退默认权限）。"""
    spec = SUBAGENT_SPECS.get(skill_id)
    if spec is None:
        raise SubagentSpecError(
            f"未知 subagent skill: {skill_id!r}，已知: {sorted(SUBAGENT_SPECS)}"
        )
    return spec


def validate_specs(registry: Any) -> None:
    """启动期校验：每个 spec 的 tool_ids 必须都已在 registry 注册（fail-loud）。

    防「spec 引用未注册工具 → 子 agent 拿到空/残缺工具集且无告警」（codex 关注的静默丢弃）。
    在所有研究工具注册完成后（A5 startup）调用。
    """
    missing: list[str] = []
    for spec in SUBAGENT_SPECS.values():
        for tid in spec.tool_ids:
            if registry.get(tid) is None:
                missing.append(f"{spec.skill_id}:{tid}")
    if missing:
        raise SubagentSpecError(
            f"subagent spec 引用了未注册工具: {missing}（请先在 build_registry 注册）"
        )
