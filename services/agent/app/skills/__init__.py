"""Skill 加载层 — 阶段 5-2a

提供渐进式 Skill 披露机制：
  1. 启动时注入 skill 索引（name + description）到 system prompt
  2. 用到才读 SKILL.md 正文（渐进披露）

文件系统护栏：
  - 限 manifest 白名单（SKILL_MANIFEST 中定义的名称）
  - 禁 ".." 与绝对路径逃逸
  - 固定根目录为本模块所在目录
  - reference 文件内容当不可信文本处理（注入到 <skill_content> 标签中）
"""
from .loader import (
    load_skill,
    list_skills,
    get_skill_index_prompt,
    SkillInfo,
    SkillLoader,
    SkillLoadError,
)

__all__ = [
    "load_skill",
    "list_skills",
    "get_skill_index_prompt",
    "SkillInfo",
    "SkillLoader",
    "SkillLoadError",
]
