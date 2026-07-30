from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from ai8video.agent_skills import (
    apply_agent_skills,
    discover_agent_skills,
    list_agent_skill_slots,
    load_agent_skill,
    validate_agent_skill_catalog,
)
from ai8video.application.request_interpreter import (
    _build_request_interpretation_prompt,
)
from ai8video.breakdown import viral_breakdown
from ai8video.breakdown import viral_breakdown_shot_language
from ai8video.generation.video_prompt_planner import build_video_planning_prompt
from ai8video.knowledge.knowledge_base_agent import (
    KnowledgeBaseAgentRequest,
    KnowledgeBaseAgentResult,
    KnowledgeQualityReport,
    SourceUnit,
    build_tree_prompt,
)
from ai8video.knowledge.reviewer_agent import build_knowledge_review_prompt


class AgentSkillRegistryTests(unittest.TestCase):
    def test_skill_documents_are_included_in_package_data(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        config = tomllib.loads((project_root / "pyproject.toml").read_text("utf-8"))
        package_data = config["tool"]["setuptools"]["package-data"]["ai8video"]

        self.assertIn("agent_skills/catalog/*/*/SKILL.md", package_data)

    def test_catalog_covers_every_configured_agent_slot(self) -> None:
        metadata = validate_agent_skill_catalog()
        configured = {slot.agent_id for slot in list_agent_skill_slots()}
        discovered = {item.agent_id for item in metadata}

        self.assertEqual(discovered, configured)
        self.assertEqual(len(metadata), len(configured))

    def test_discovery_reads_frontmatter_without_loading_full_documents(self) -> None:
        with mock.patch.object(
            Path,
            "read_text",
            side_effect=AssertionError("元数据扫描不应读取完整 Skill"),
        ):
            metadata = discover_agent_skills("planner")

        self.assertEqual([item.name for item in metadata], ["plan-video-content"])
        self.assertEqual(metadata[0].version, "2.0.0")
        self.assertEqual(metadata[0].kind, "policy")
        self.assertEqual(metadata[0].capabilities, ("planner.plan-video-content",))
        self.assertIn("drama-skills", metadata[0].source)

    def test_default_skill_is_injected_but_placeholder_remains_inactive(self) -> None:
        prompt = apply_agent_skills("planner", "原始任务")

        self.assertTrue(prompt.startswith('<agent-skills agent="planner">'))
        self.assertIn('<skill name="plan-video-content">', prompt)
        self.assertTrue(prompt.endswith("原始任务"))
        self.assertEqual(apply_agent_skills("supervisor", "原始任务"), "原始任务")

    def test_skill_loading_is_isolated_by_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "planner" / "custom-plan"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: custom-plan\n"
                "description: 测试规划技能。\n"
                "---\n\n"
                "# 规划\n\n只服务 planner。\n",
                encoding="utf-8",
            )

            loaded = load_agent_skill("planner", "custom-plan", root=root)
            self.assertIn("只服务 planner", loaded.instructions)
            with self.assertRaises(KeyError):
                load_agent_skill("intent-agent", "custom-plan", root=root)

    def test_active_agent_prompt_entrypoints_load_their_own_skills(self) -> None:
        request_prompt = _build_request_interpretation_prompt("生成两条视频")
        planner_prompt = build_video_planning_prompt("素材", 2)
        source_unit = SourceUnit(1, "paragraph", "证据")
        request = KnowledgeBaseAgentRequest(1, "文档", "证据")
        knowledge_prompt = build_tree_prompt(request, [source_unit])
        proposal = KnowledgeBaseAgentResult(
            tree={"tree": []},
            leaves=[],
            source_units=[source_unit],
            quality=KnowledgeQualityReport(0, 1, 0, 0, 0),
        )
        review_prompt = build_knowledge_review_prompt(request, proposal)
        shot_messages = viral_breakdown_shot_language._build_analysis_messages(
            {"transcript": {"text": "台词"}, "selectedFrames": [], "rowBatches": []}
        )
        script_messages = viral_breakdown._build_script_guess_messages(
            "台词",
            "镜头语言",
        )

        expected = (
            (request_prompt, 'agent="intent-agent"'),
            (planner_prompt, 'agent="planner"'),
            (knowledge_prompt, 'agent="knowledge-base"'),
            (review_prompt, 'agent="reviewer"'),
            (shot_messages[0]["content"], 'agent="viral-shot-language"'),
            (
                script_messages[0]["content"],
                'agent="viral-script-reconstruction"',
            ),
        )
        for prompt, marker in expected:
            with self.subTest(marker=marker):
                self.assertIn("<agent-skills", prompt)
                self.assertIn(marker, prompt)


if __name__ == "__main__":
    unittest.main()
