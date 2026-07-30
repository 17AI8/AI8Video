"""轻量 Skill 注册表：只扫描元数据，调用时才读取完整说明。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CATALOG_ROOT = Path(__file__).resolve().parent / "catalog"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CAPABILITY_PATTERN = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")
FRONTMATTER_ALLOWED_KEYS = {
    "name",
    "description",
    "version",
    "license",
    "kind",
    "capabilities",
    "source",
}


@dataclass(frozen=True)
class AgentSkillSlot:
    agent_id: str
    label: str
    default_skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillMetadata:
    agent_id: str
    name: str
    description: str
    path: Path
    version: str = "1.0.0"
    license: str = ""
    kind: str = "policy"
    capabilities: tuple[str, ...] = ()
    source: str = ""


@dataclass(frozen=True)
class LoadedAgentSkill:
    metadata: SkillMetadata
    instructions: str


AGENT_SKILL_SLOTS = (
    AgentSkillSlot("supervisor", "任务监督 Agent"),
    AgentSkillSlot("intent-agent", "意图理解 Agent", ("interpret-request",)),
    AgentSkillSlot("planner", "文本规划 Agent", ("plan-video-content",)),
    AgentSkillSlot("knowledge-base", "知识库 Agent", ("structure-knowledge",)),
    AgentSkillSlot("reviewer", "独立审核 Agent", ("review-knowledge",)),
    AgentSkillSlot("viral-shot-language", "爆款镜头语言 Agent", ("analyze-shot-language",)),
    AgentSkillSlot(
        "viral-script-reconstruction",
        "爆款猜剧本 Agent",
        ("reconstruct-script",),
    ),
    AgentSkillSlot("viral-script-knowledge", "爆款剧本知识 Agent"),
    AgentSkillSlot("html-motion", "HTML 动效 Agent"),
    AgentSkillSlot("smart-image", "智能修图 Agent"),
    AgentSkillSlot("hot-topic", "热点雷达 Agent"),
    AgentSkillSlot("narration-review", "口播审核 Agent"),
    AgentSkillSlot("output-review", "成片输出审核 Agent"),
    AgentSkillSlot("script-knowledge-query", "剧本知识检索 Agent"),
    AgentSkillSlot("script-knowledge-rerank", "剧本知识重排 Agent"),
)


def list_agent_skill_slots() -> tuple[AgentSkillSlot, ...]:
    return AGENT_SKILL_SLOTS


def get_agent_skill_slot(agent_id: str) -> AgentSkillSlot:
    normalized = _validate_slug(agent_id, "agent_id")
    for slot in AGENT_SKILL_SLOTS:
        if slot.agent_id == normalized:
            return slot
    raise KeyError(f"未知 Agent Skill 槽位：{normalized}")


def discover_agent_skills(
    agent_id: str | None = None,
    *,
    root: Path | None = None,
) -> tuple[SkillMetadata, ...]:
    catalog_root = Path(root or CATALOG_ROOT).resolve()
    agent_ids = _discovery_agent_ids(agent_id, catalog_root)
    metadata: list[SkillMetadata] = []
    for current_agent_id in agent_ids:
        agent_dir = catalog_root / current_agent_id
        if not agent_dir.is_dir():
            continue
        for skill_path in sorted(agent_dir.glob("*/SKILL.md")):
            metadata.append(_read_skill_metadata(current_agent_id, skill_path))
    return tuple(metadata)


def load_agent_skill(
    agent_id: str,
    skill_name: str,
    *,
    root: Path | None = None,
) -> LoadedAgentSkill:
    normalized_name = _validate_slug(skill_name, "skill_name")
    metadata = _find_skill_metadata(agent_id, normalized_name, root=root)
    document = metadata.path.read_text(encoding="utf-8")
    _, instructions = _split_skill_document(document, metadata.path)
    if not instructions:
        raise ValueError(f"Skill 正文为空：{metadata.path}")
    if "</skill>" in instructions or "</agent-skills>" in instructions:
        raise ValueError(f"Skill 正文包含保留边界标记：{metadata.path}")
    return LoadedAgentSkill(metadata=metadata, instructions=instructions)


def apply_agent_skills(
    agent_id: str,
    prompt: str,
    *,
    skill_names: Iterable[str] | None = None,
    root: Path | None = None,
) -> str:
    slot = get_agent_skill_slot(agent_id)
    selected = tuple(skill_names) if skill_names is not None else slot.default_skills
    selected = tuple(dict.fromkeys(
        str(name).strip()
        for name in selected
        if str(name).strip()
    ))
    if not selected:
        return str(prompt)
    loaded = [load_agent_skill(slot.agent_id, name, root=root) for name in selected]
    return f"{_format_skill_block(slot.agent_id, loaded)}\n\n{prompt}"


def validate_agent_skill_catalog(*, root: Path | None = None) -> tuple[SkillMetadata, ...]:
    metadata = discover_agent_skills(root=root)
    available = {(item.agent_id, item.name) for item in metadata}
    agents_with_skills = {item.agent_id for item in metadata}
    for slot in AGENT_SKILL_SLOTS:
        if slot.agent_id not in agents_with_skills:
            raise ValueError(f"Agent 缺少 Skill 槽位：{slot.agent_id}")
        for skill_name in slot.default_skills:
            if (slot.agent_id, skill_name) not in available:
                raise ValueError(f"默认 Skill 不存在：{slot.agent_id}/{skill_name}")
    return metadata


def _discovery_agent_ids(agent_id: str | None, root: Path) -> tuple[str, ...]:
    if agent_id is not None:
        return (_validate_slug(agent_id, "agent_id"),)
    configured = [slot.agent_id for slot in AGENT_SKILL_SLOTS]
    extras = (
        sorted(
            path.name
            for path in root.iterdir()
            if path.is_dir() and path.name not in configured
        )
        if root.is_dir()
        else []
    )
    return tuple(configured + extras)


def _find_skill_metadata(
    agent_id: str,
    skill_name: str,
    *,
    root: Path | None,
) -> SkillMetadata:
    matches = [
        item
        for item in discover_agent_skills(agent_id, root=root)
        if item.name == skill_name
    ]
    if not matches:
        raise KeyError(f"Agent Skill 不存在：{agent_id}/{skill_name}")
    return matches[0]


def _read_skill_metadata(agent_id: str, skill_path: Path) -> SkillMetadata:
    frontmatter = _read_frontmatter(skill_path)
    name = _validate_slug(frontmatter.get("name", ""), "name")
    if name != skill_path.parent.name:
        raise ValueError(f"Skill 名称与目录不一致：{skill_path}")
    description = frontmatter.get("description", "").strip()
    if not description:
        raise ValueError(f"Skill 缺少 description：{skill_path}")
    kind = frontmatter.get("kind", "policy").strip() or "policy"
    if kind not in {"policy", "workflow"}:
        raise ValueError(f"Skill kind 不合法：{skill_path}")
    capabilities = tuple(dict.fromkeys(
        item.strip()
        for item in frontmatter.get("capabilities", "").split(",")
        if item.strip()
    ))
    if any(not CAPABILITY_PATTERN.fullmatch(item) for item in capabilities):
        raise ValueError(f"Skill capabilities 不合法：{skill_path}")
    return SkillMetadata(
        agent_id=agent_id,
        name=name,
        description=description,
        path=skill_path.resolve(),
        version=frontmatter.get("version", "1.0.0").strip() or "1.0.0",
        license=frontmatter.get("license", "").strip(),
        kind=kind,
        capabilities=capabilities,
        source=frontmatter.get("source", "").strip(),
    )


def _read_frontmatter(path: Path) -> dict[str, str]:
    lines: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        if handle.readline().strip() != "---":
            raise ValueError(f"Skill 缺少 YAML frontmatter：{path}")
        for line_number, line in enumerate(handle, start=2):
            if line.strip() == "---":
                return _parse_frontmatter_lines(lines, path)
            lines.append(line.rstrip("\n"))
            if line_number > 64 or sum(map(len, lines)) > 8192:
                raise ValueError(f"Skill frontmatter 过大：{path}")
    raise ValueError(f"Skill frontmatter 未闭合：{path}")


def _parse_frontmatter_lines(lines: list[str], path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in lines:
        if not raw_line.strip():
            continue
        key, separator, value = raw_line.partition(":")
        key = key.strip()
        if not separator or key not in FRONTMATTER_ALLOWED_KEYS:
            raise ValueError(f"Skill frontmatter 字段不合法：{path}")
        parsed[key] = _strip_optional_quotes(value.strip())
    return parsed


def _split_skill_document(document: str, path: Path) -> tuple[dict[str, str], str]:
    lines = document.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Skill 缺少 YAML frontmatter：{path}")
    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], 1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ValueError(f"Skill frontmatter 未闭合：{path}") from exc
    frontmatter = _parse_frontmatter_lines(lines[1:closing_index], path)
    return frontmatter, "\n".join(lines[closing_index + 1:]).strip()


def _format_skill_block(agent_id: str, skills: list[LoadedAgentSkill]) -> str:
    parts = [f'<agent-skills agent="{agent_id}">']
    for skill in skills:
        parts.extend(
            [
                f'<skill name="{skill.metadata.name}">',
                skill.instructions,
                "</skill>",
            ]
        )
    parts.append("</agent-skills>")
    return "\n".join(parts)


def _validate_slug(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > 64 or not SLUG_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} 必须是小写连字符 slug")
    return normalized


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
