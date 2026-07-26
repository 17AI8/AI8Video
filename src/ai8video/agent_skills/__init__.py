"""AI8video 内置 Agent Skill 注册、发现与按需加载入口。"""

from ai8video.agent_skills.registry import (
    AGENT_SKILL_SLOTS,
    AgentSkillSlot,
    LoadedAgentSkill,
    SkillMetadata,
    apply_agent_skills,
    discover_agent_skills,
    get_agent_skill_slot,
    list_agent_skill_slots,
    load_agent_skill,
    validate_agent_skill_catalog,
)

__all__ = [
    "AGENT_SKILL_SLOTS",
    "AgentSkillSlot",
    "LoadedAgentSkill",
    "SkillMetadata",
    "apply_agent_skills",
    "discover_agent_skills",
    "get_agent_skill_slot",
    "list_agent_skill_slots",
    "load_agent_skill",
    "validate_agent_skill_catalog",
]
