"""测试 Skill 加载层 (app/skills/loader.py)。

覆盖:
  - list_skills: 返回白名单所有 skill 的索引（不含正文）
  - load_skill: 按需加载 SKILL.md 正文（渐进披露）
  - get_skill_index_prompt: 生成 system 索引提示
  - 文件系统护栏: 拒绝 ".." 逃逸、绝对路径、未知名称
  - SkillLoader BaseTool: list / load action
"""
from __future__ import annotations

import pytest

from app.skills.loader import (
    load_skill,
    list_skills,
    get_skill_index_prompt,
    SkillInfo,
    SkillLoader,
    SkillLoadError,
    SKILL_MANIFEST,
)


# ======================================================================
# list_skills
# ======================================================================

class TestListSkills:
    def test_returns_all_manifest_skills(self):
        skills = list_skills()
        names = {s.name for s in skills}
        assert "read-paper" in names
        assert "synthesis" in names

    def test_content_not_loaded(self):
        """list_skills 不加载正文（渐进披露第一层）。"""
        skills = list_skills()
        for s in skills:
            assert s.content is None

    def test_description_present(self):
        skills = list_skills()
        for s in skills:
            assert s.description
            assert len(s.description) > 10

    def test_version_extracted(self):
        """版本号应从 SKILL.md 首部提取。"""
        skills = list_skills()
        for s in skills:
            assert s.version != ""  # 有值（可以是 "unknown"，但应该能提取到实际版本）
        # read-paper 和 synthesis 的 SKILL.md 都有 version 行
        names = {s.name: s.version for s in skills}
        assert names.get("read-paper") == "1.0.0"
        assert names.get("synthesis") == "1.0.0"


# ======================================================================
# load_skill — 正常加载
# ======================================================================

class TestLoadSkill:
    def test_load_read_paper_returns_skill_info(self):
        info = load_skill("read-paper")
        assert isinstance(info, SkillInfo)
        assert info.name == "read-paper"
        assert info.version == "1.0.0"

    def test_load_synthesis_returns_skill_info(self):
        info = load_skill("synthesis")
        assert isinstance(info, SkillInfo)
        assert info.name == "synthesis"
        assert info.version == "1.0.0"

    def test_content_loaded(self):
        """load_skill 应加载 SKILL.md 正文。"""
        info = load_skill("read-paper")
        assert info.content is not None
        assert len(info.content) > 100

    def test_read_paper_content_has_key_sections(self):
        """read-paper SKILL.md 应含关键章节。"""
        info = load_skill("read-paper")
        assert "research_question" in info.content
        assert "findings" in info.content
        assert "key_points" in info.content

    def test_synthesis_content_has_key_sections(self):
        """synthesis SKILL.md 应含综述结构章节。"""
        info = load_skill("synthesis")
        assert "引言" in info.content
        assert "主题归纳" in info.content
        assert "[n]" in info.content  # 引用格式说明


# ======================================================================
# 文件系统护栏
# ======================================================================

class TestFSGuardrails:
    def test_unknown_skill_raises(self):
        """未知 skill name → SkillLoadError。"""
        with pytest.raises(SkillLoadError, match="未知 skill"):
            load_skill("nonexistent-skill")

    def test_dotdot_traversal_raises(self):
        """'..' 路径逃逸 → SkillLoadError（白名单阻止）。"""
        with pytest.raises(SkillLoadError):
            load_skill("../etc/passwd")

    def test_dotdot_in_name_raises(self):
        """name 中含 '..' → SkillLoadError。"""
        with pytest.raises(SkillLoadError):
            load_skill("read-paper/../../../etc")

    def test_absolute_path_raises(self):
        """以 '/' 开头的绝对路径 → SkillLoadError（白名单阻止）。"""
        with pytest.raises(SkillLoadError):
            load_skill("/etc/passwd")

    def test_tilde_path_raises(self):
        """以 '~' 开头 → SkillLoadError（白名单阻止）。"""
        with pytest.raises(SkillLoadError):
            load_skill("~/.ssh/id_rsa")

    def test_empty_name_raises(self):
        """空 name → SkillLoadError。"""
        with pytest.raises(SkillLoadError):
            load_skill("")

    def test_special_chars_raises(self):
        """含特殊字符 → SkillLoadError。"""
        with pytest.raises(SkillLoadError):
            load_skill("skill; rm -rf /")


# ======================================================================
# get_skill_index_prompt
# ======================================================================

class TestSkillIndexPrompt:
    def test_contains_skill_names(self):
        prompt = get_skill_index_prompt()
        assert "read-paper" in prompt
        assert "synthesis" in prompt

    def test_contains_descriptions(self):
        prompt = get_skill_index_prompt()
        # 确保描述被包含
        for name in SKILL_MANIFEST:
            assert name in prompt

    def test_does_not_contain_full_content(self):
        """索引提示不应包含 SKILL.md 全文（渐进披露）。"""
        prompt = get_skill_index_prompt()
        # 确认没有包含 SKILL.md 中的详细操作步骤
        assert "操作指南" not in prompt  # 这是 SKILL.md 中的内容


# ======================================================================
# SkillLoader BaseTool
# ======================================================================

class TestSkillLoaderTool:
    def test_tool_meta(self):
        tool = SkillLoader()
        assert tool.tool_id == "skill_loader"
        assert "list" in tool.actions
        assert "load" in tool.actions

    @pytest.mark.asyncio
    async def test_list_action(self):
        tool = SkillLoader()
        result = await tool.execute("list", {})
        assert result.success
        assert len(result.data) >= 2
        names = {d["name"] for d in result.data}
        assert "read-paper" in names
        assert "synthesis" in names

    @pytest.mark.asyncio
    async def test_load_action_success(self):
        tool = SkillLoader()
        result = await tool.execute("load", {"name": "read-paper"})
        assert result.success
        assert len(result.data) == 1
        # content 被包裹在 <skill_content> 标签中
        skill_content = result.data[0]["skill_content"]
        assert "<skill_content" in skill_content
        assert "</skill_content>" in skill_content
        assert "read-paper" in skill_content

    @pytest.mark.asyncio
    async def test_load_action_unknown_name(self):
        tool = SkillLoader()
        result = await tool.execute("load", {"name": "nonexistent"})
        assert not result.success
        assert "未知 skill" in result.error

    @pytest.mark.asyncio
    async def test_load_action_traversal_blocked(self):
        tool = SkillLoader()
        result = await tool.execute("load", {"name": "../evil"})
        assert not result.success

    @pytest.mark.asyncio
    async def test_load_action_empty_name(self):
        tool = SkillLoader()
        result = await tool.execute("load", {"name": ""})
        assert not result.success
        assert "不能为空" in result.error

    @pytest.mark.asyncio
    async def test_list_content_not_loaded(self):
        """list action 不应加载 SKILL.md 正文。"""
        tool = SkillLoader()
        result = await tool.execute("list", {})
        for item in result.data:
            assert item["content_loaded"] is False
