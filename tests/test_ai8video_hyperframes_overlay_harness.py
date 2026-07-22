from __future__ import annotations

import copy
import json
import re
import unittest

from ai8video.media.motion.hyperframes_overlay_composition import build_composition_html
from ai8video.media.motion.hyperframes_overlay_harness import (
    _build_generation_prompt,
    _normalize_artifact,
    build_hyperframes_overlay,
    revise_hyperframes_overlay,
)
from ai8video.media.motion.hyperframes_overlay_prompts import HTML_MOTION_SYSTEM_PROMPT
from ai8video.media.motion.hyperframes_overlay_semantic import dialogue_text_blocks
from ai8video.core.models import EpisodePrompt


def _scene(index: int, start: float, end: float, zone: str, ease_a: str, ease_b: str) -> dict:
    prefix = f"scene-{index}"
    return {
        "start": start,
        "end": end,
        "zone": zone,
        "roles": ["content", "structure", "decorative"],
        "html": (
            f'<h1 id="{prefix}-title" class="title">关键沟通</h1>'
            f'<div id="{prefix}-rule" class="rule"></div>'
            f'<svg id="{prefix}-orbit" class="orbit" viewBox="0 0 100 100" aria-hidden="true">'
            '<circle cx="50" cy="50" r="42" fill="none" stroke="#78A7FF" stroke-width="3"></circle></svg>'
        ),
        "css": (
            f"#{prefix}-title{{font-size:64px;line-height:1.05;color:var(--text);font-weight:800}}"
            f"#{prefix}-rule{{width:180px;height:4px;background:var(--accent);margin-top:18px}}"
            f"#{prefix}-orbit{{position:absolute;width:160px;height:160px;right:12px;top:12px;opacity:.7}}"
        ),
        "animations": [
            {
                "target": f"#{prefix}-title",
                "kind": "entrance",
                "at": 0.1,
                "duration": 0.5,
                "from": {"x": -40, "autoAlpha": 0},
                "to": {"x": 0, "autoAlpha": 1, "ease": ease_a},
            },
            {
                "target": f"#{prefix}-rule",
                "kind": "entrance",
                "at": 0.25,
                "duration": 0.55,
                "from": {"scaleX": 0, "autoAlpha": 0, "transformOrigin": "left center"},
                "to": {"scaleX": 1, "autoAlpha": 1, "ease": ease_b},
            },
            {
                "target": f"#{prefix}-orbit",
                "kind": "entrance",
                "at": 0.05,
                "duration": 0.6,
                "from": {"scale": 0.7, "autoAlpha": 0},
                "to": {"scale": 1, "autoAlpha": 0.7, "ease": "back.out(1.4)"},
            },
            {
                "target": f"#{prefix}-orbit",
                "kind": "ambient",
                "at": 0.65,
                "duration": (end - start) - 0.7,
                "to": {"rotation": 24, "scale": 1.08, "ease": "sine.inOut"},
            },
        ],
    }


def _artifact() -> dict:
    return {
        "design": {
            "candidates": ["编辑线框", "数据节点", "温暖对话"],
            "chosen": "编辑线框",
            "concept": "用边缘线框与节点补充沟通叙事",
            "palette": {"accent": "#78A7FF", "support": "#FFB76B", "text": "#F7F3EC"},
            "typography": "editorial",
        },
        "scenes": [
            _scene(1, 0.0, 2.0, "top-left", "power3.out", "expo.out"),
            _scene(2, 2.0, 4.0, "bottom-right", "power2.out", "circ.out"),
        ],
    }


class AI8VideoHyperframesOverlayHarnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.media = {"width": 720, "height": 1280, "durationSeconds": 4.0}
        self.episode = EpisodePrompt(index=1, title="第一集", prompt="办公室中围绕沟通困境展开讨论")

    def test_harness_builds_inline_html_css_and_seekable_waapi_plan(self) -> None:
        responses = iter([
            json.dumps(_artifact(), ensure_ascii=False),
            json.dumps({
                "scores": {"clarity": 4, "hierarchy": 4, "typography": 4, "motion": 5, "brand": 4},
                "notes": ["透明叠加区域清晰"],
                "revisedArtifact": None,
            }, ensure_ascii=False),
        ])
        result = build_hyperframes_overlay(
            lambda _prompt: next(responses),
            self.episode,
            self.media,
            dialogue_text="关键沟通需要保留完整上下文",
        )

        self.assertIn('<style>html,body{', result.composition_html)
        self.assertIn('class="hf-zone zone-top-left"', result.composition_html)
        self.assertIn('src="./waapi-timeline-runtime.js"', result.composition_html)
        self.assertIn("data-no-timeline", result.composition_html)
        self.assertIn('"target":"#scene-1-title"', result.composition_html)
        self.assertIn("window.AI8WaapiTimeline.mount", result.composition_html)
        self.assertNotIn("repeat", result.composition_html)
        self.assertNotIn("-webkit-line-clamp", result.composition_html)
        self.assertNotIn(".hf-zone h1,.hf-zone h2,.hf-zone h3,.hf-zone p,.hf-zone small{position:relative!important;left:auto!important;right:auto!important;top:auto!important;bottom:auto!important;height:auto!important;margin:0 0 12px;max-width:100%;overflow:hidden", result.composition_html)
        self.assertIn("vector-effect:non-scaling-stroke", result.composition_html)
        self.assertEqual(result.summary["harness"], "hyperframes-overlay-v1")
        self.assertEqual(result.summary["sceneCount"], 2)
        self.assertEqual(result.summary["critiqueScores"]["motion"], 5)

    def test_harness_uses_revised_artifact_when_critique_is_low(self) -> None:
        original = _artifact()
        revised = copy.deepcopy(original)
        revised["design"]["chosen"] = "数据节点"
        responses = iter([
            json.dumps(original, ensure_ascii=False),
            json.dumps({
                "scores": {"clarity": 3, "hierarchy": 4, "typography": 4, "motion": 4, "brand": 4},
                "notes": ["信息焦点不足"],
                "revisedArtifact": revised,
            }, ensure_ascii=False),
            json.dumps({
                "scores": {"clarity": 4, "hierarchy": 5, "typography": 4, "motion": 4, "brand": 4},
                "notes": ["修订后焦点清晰"],
                "revisedArtifact": None,
            }, ensure_ascii=False),
        ])

        result = build_hyperframes_overlay(
            lambda _prompt: next(responses),
            self.episode,
            self.media,
            dialogue_text="关键沟通需要保留完整上下文",
        )

        self.assertEqual(result.artifact["design"]["chosen"], "数据节点")
        self.assertEqual(result.summary["critiqueScores"]["hierarchy"], 5)

    def test_harness_uses_last_valid_revision_when_critique_never_converges(self) -> None:
        revisions = []
        for chosen in ("数据节点", "温暖对话", "编辑线框"):
            revision = copy.deepcopy(_artifact())
            revision["design"]["chosen"] = chosen
            revisions.append(revision)
        low_score = {"clarity": 3, "hierarchy": 3, "typography": 3, "motion": 3, "brand": 3}
        responses = iter([
            json.dumps(_artifact(), ensure_ascii=False),
            *[
                json.dumps({"scores": low_score, "notes": ["继续优化"], "revisedArtifact": revision}, ensure_ascii=False)
                for revision in revisions
            ],
        ])

        result = build_hyperframes_overlay(
            lambda _prompt: next(responses), self.episode, self.media, dialogue_text="关键沟通需要保留完整上下文"
        )

        self.assertEqual(result.artifact["design"]["chosen"], "编辑线框")
        self.assertFalse(result.summary["critiqueConverged"])

    def test_harness_can_skip_optional_critique_for_fast_regeneration(self) -> None:
        calls: list[str] = []

        def llm(prompt: str) -> str:
            calls.append(prompt)
            return json.dumps(_artifact(), ensure_ascii=False)

        result = build_hyperframes_overlay(
            llm,
            self.episode,
            self.media,
            dialogue_text="关键沟通需要保留完整上下文",
            critique_enabled=False,
        )

        self.assertEqual(len(calls), 1)
        self.assertTrue(result.summary["critiqueSkipped"])
        self.assertIsNone(result.summary["critiqueConverged"])

    def test_semantic_contract_uses_fixed_components_and_current_dialogue(self) -> None:
        semantic = {
            "theme": "orbit",
            "anchor": "top-right",
            "motion": "orbit",
            "density": "rich",
            "decorations": ["ring", "dot", "line"],
            "palette": {"accent": "#78A7FF", "support": "#FFB76B", "text": "#F7F3EC"},
            "beats": [
                {"question": "沟通总等待？", "result": "自然衔接！"},
                {"question": "对话总停顿？", "result": "信息开始顺畅！"},
            ],
        }
        result = build_hyperframes_overlay(
            lambda _prompt: json.dumps(semantic, ensure_ascii=False),
            self.episode,
            {**self.media, "durationSeconds": 10.0},
            dialogue_text="沟通总等待。对话总停顿。自然衔接。信息开始顺畅。后续内容不显示。",
            critique_enabled=False,
        )

        self.assertTrue(result.summary["semanticContract"])
        self.assertEqual(result.artifact["layoutMode"], "fixed-semantic")
        self.assertIn('class="hf-zone zone-top-right hf-fixed-zone"', result.composition_html)
        self.assertNotIn('class="hf-zone zone-bottom-band hf-fixed-zone"', result.composition_html)
        title_text = "".join(re.findall(
            r'id="scene-\d+-(?:question|result)"[^>]*>.*?</div>',
            result.composition_html,
            flags=re.S,
        ))
        title_glyphs = "".join(re.findall(r'class="hf-line(?: hf-bang)?">([^<]*)</span>', title_text))
        plain = title_glyphs.replace("？", "").replace("！", "")
        self.assertIn("沟通总等", plain)
        self.assertIn("自然衔", plain)
        self.assertIn("对话总停", plain)
        self.assertIn("？", title_glyphs)
        self.assertIn("！", title_glyphs)
        self.assertNotIn("后续内容不显示", result.composition_html)
        self.assertIn("hf-network-orbit", result.composition_html)
        self.assertNotIn("hf-panel", result.composition_html)
        self.assertNotIn("hf-grid", result.composition_html)
        self.assertIn("window.AI8WaapiTimeline.mount", result.composition_html)
        self.assertEqual(result.summary["harness"], "hyperframes-overlay-v5-reviewed-json")
        self.assertEqual(result.summary["sceneCount"], 2)
        self.assertGreaterEqual(result.summary["elementCount"], 8)
        self.assertGreaterEqual(result.summary["animationCount"], 12)
        self.assertEqual(result.motion_manifest["duration"], 10.0)
        self.assertIn('id="scene-1-question"', result.composition_html)
        self.assertIn('id="scene-2-result"', result.composition_html)
        self.assertIn("#hf-scene-1 .hf-title", result.composition_html)

    def test_semantic_text_blocks_keep_complete_clauses_and_flower_text_style(self) -> None:
        blocks = dialogue_text_blocks("先提出问题。再解释变化。最后给出结论。", max_blocks=2)
        self.assertEqual(len(blocks), 2)
        self.assertIn("先提出问题。", blocks[0]["source"])
        self.assertIn("最后给出结论。", blocks[1]["source"])
        result = build_hyperframes_overlay(
            lambda _prompt: json.dumps({
                "theme": "signal",
                "beats": [{"question": "表达总卡顿？", "result": "清晰抵达！"}],
            }, ensure_ascii=False),
            self.episode,
            {**self.media, "textStyle": {"textColor": "#FFEE43", "strokeColor": "#121826", "strokeWidth": 6}},
            dialogue_text="信息表达总卡顿。当前信息清晰抵达。",
            critique_enabled=False,
        )
        self.assertIn("--motion-text:#FFEE43", result.composition_html)
        self.assertIn("--motion-stroke:#121826", result.composition_html)
        self.assertIn("--motion-stroke-width:0.75px", result.composition_html)
        # balanced default: no heavy flower-text stroke; readability from shadow.
        self.assertIn("-webkit-text-stroke:0", result.composition_html)
        self.assertIn("font-weight:700", result.composition_html)
        self.assertIn("hf-line", result.composition_html)

    def test_semantic_uses_large_text_and_only_lightweight_ornaments(self) -> None:
        result = build_hyperframes_overlay(
            lambda _prompt: json.dumps({
                "theme": "editorial",
                "anchor": "top-left",
                "density": "balanced",
                "beats": [{"question": "表达总卡顿？", "result": "表达更清晰！"}],
            }, ensure_ascii=False),
            self.episode,
            self.media,
            dialogue_text="信息表达总卡顿。信息表达更清晰。",
            critique_enabled=False,
        )

        composition = result.composition_html
        self.assertIn(".hf-title-row{display:flex", composition)
        self.assertIn("hf-density-balanced", composition)
        self.assertNotIn("hf-graphic", composition)
        self.assertNotIn("hf-accent", composition)
        self.assertIn("word-break:normal", composition)
        self.assertNotIn("hf-panel", composition)
        self.assertNotIn("linear-gradient(135deg", composition)
        self.assertRegex(composition, r"\.hf-title\{[^}]*font-size:(?:3[2-9]|[4-8][0-9])px")
        self.assertIn("line-height:1.08", composition)
        self.assertIn("font-weight:700", composition)
        self.assertIn("color:var(--motion-text)", composition)
        self.assertIn("-webkit-text-stroke:0", composition)
        self.assertIn('id="scene-1-question"', composition)
        self.assertIn('id="scene-1-result"', composition)
        self.assertIn('"target":"#scene-1-question .hf-line"', composition)
        self.assertIn('"target":"#scene-1-result .hf-line"', composition)
        self.assertIn('"stagger":0.07', composition)
        self.assertRegex(composition, r"\.hf-stage\{[^}]*width:(?:2[4-9]|3[0-9]|4[0-2])(?:\.\d+)?%")
        self.assertNotRegex(composition, r"\.hf-stage\{[^}]*height:7[0-9]")
        self.assertRegex(composition, r"\.hf-card-content\{[^}]*left:4(?:\.0)?%")
        self.assertNotRegex(composition, r"\.hf-card-content\{[^}]*left:(?:3[0-9]|4[0-9]|5[0-9])(?:\.0)?%")

    def test_harness_revises_artifact_from_real_validation_error(self) -> None:
        revision = copy.deepcopy(_artifact())
        revision["design"]["chosen"] = "数据节点"

        result = revise_hyperframes_overlay(
            lambda _prompt: json.dumps(revision, ensure_ascii=False),
            _normalize_artifact(_artifact(), self.media),
            self.episode,
            self.media,
            dialogue_text="关键沟通需要保留完整上下文",
            validation_error="Text content is clipped",
            font_family="AI8VideoFlower",
        )

        self.assertTrue(result.summary["validationRevision"])
        self.assertEqual(result.artifact["design"]["chosen"], "数据节点")

    def test_harness_derives_missing_scene_role_metadata(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["roles"] = []

        normalized = _normalize_artifact(artifact, self.media)

        self.assertEqual(normalized["scenes"][0]["roles"], ["content", "decorative", "structure"])

    def test_harness_allows_small_but_renderable_scene(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["html"] = '<h1 id="scene-1-title">关键沟通</h1>'
        artifact["scenes"][0]["animations"] = [
            {"target": "#scene-1-title", "kind": "entrance", "at": 0, "duration": 0.5,
             "from": {"autoAlpha": 0}, "to": {"autoAlpha": 1, "ease": "power3.out"}},
            {"target": "#scene-1-title", "kind": "ambient", "at": 0.5, "duration": 1.0,
             "from": {}, "to": {"scale": 1.03, "ease": "sine.inOut"}},
        ]

        normalized = _normalize_artifact(artifact, self.media)

        self.assertEqual(len(normalized["scenes"][0]["ids"]), 1)

    def test_rejects_script_and_drops_external_css_but_allows_static_ids(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["html"] += '<script id="scene-1-bad">alert(1)</script>'
        with self.assertRaisesRegex(ValueError, "标签"):
            _normalize_artifact(artifact, self.media)

        artifact = _artifact()
        artifact["scenes"][0]["css"] += ".remote{background:url(https://example.com/a.png)}"
        normalized = _normalize_artifact(artifact, self.media)
        self.assertEqual(normalized["scenes"][0]["css"], "")

        artifact = _artifact()
        artifact["scenes"][0]["html"] += '<div id="scene-1-unplanned"></div>'
        normalized = _normalize_artifact(artifact, self.media)
        self.assertIn("scene-1-unplanned", normalized["scenes"][0]["ids"])

    def test_static_ids_are_not_added_to_waapi_plan(self) -> None:
        artifact = _normalize_artifact(_artifact(), self.media)
        artifact["scenes"][0]["html"] += '<div id="scene-1-static-frame"></div>'
        artifact["scenes"][0]["ids"].append("scene-1-static-frame")

        composition = build_composition_html(artifact, self.media)

        self.assertIn('id="scene-1-static-frame"', composition)
        self.assertNotIn('"target":"#scene-1-static-frame"', composition)

    def test_composition_uses_only_declared_selected_font(self) -> None:
        composition = build_composition_html(_normalize_artifact(_artifact(), self.media), self.media, font_family="AI8VideoFlower")

        self.assertIn("@font-face{font-family:'AI8VideoFlower'", composition)
        self.assertIn("font-family:'AI8VideoFlower'", composition)
        self.assertNotIn("PingFang SC", composition)

    def test_keeps_safe_inline_style_and_strips_unsafe_declarations(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["html"] += (
            '<div id="scene-1-caption" style="position:absolute;left:12px;opacity:.8"></div>'
        )
        normalized = _normalize_artifact(artifact, self.media)
        self.assertIn('style="opacity:.8"', normalized["scenes"][0]["html"])

        artifact["scenes"][0]["css"] += "#scene-1-rule{position:absolute;left:600px;width:720px;color:#fff}"
        normalized = _normalize_artifact(artifact, self.media)
        self.assertIn("position:absolute", normalized["scenes"][0]["css"])
        self.assertIn("width:720px", normalized["scenes"][0]["css"])

        for unsafe_style in (
            "background:url(https://example.com/a.png)",
            "animation-name:pulse",
            "position:fixed",
        ):
            artifact = _artifact()
            artifact["scenes"][0]["html"] += (
                f'<div id="scene-1-bad-style" style="position:absolute;{unsafe_style}"></div>'
            )
            normalized = _normalize_artifact(artifact, self.media)
            fragment = normalized["scenes"][0]["html"]
            self.assertNotIn("position:absolute", fragment)
            self.assertNotIn(unsafe_style, fragment)

    def test_allows_svg_presentation_attributes_but_rejects_external_values(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["html"] = artifact["scenes"][0]["html"].replace(
            'stroke-width="3"',
            'stroke-width="3" opacity=".7" stroke-dasharray="8 4" vector-effect="non-scaling-stroke"',
        )
        normalized = _normalize_artifact(artifact, self.media)
        self.assertIn('opacity=".7"', normalized["scenes"][0]["html"])

        artifact = _artifact()
        artifact["scenes"][0]["html"] = artifact["scenes"][0]["html"].replace(
            'fill="none"', 'fill="url(https://example.com/paint.svg)"',
        )
        with self.assertRaisesRegex(ValueError, "外部或可执行资源"):
            _normalize_artifact(artifact, self.media)

    def test_harness_scopes_model_ids_instead_of_rejecting_valid_scene(self) -> None:
        artifact = _artifact()
        scene = artifact["scenes"][0]
        scene["html"] = scene["html"].replace("scene-1-title", "hero-title")
        scene["css"] = scene["css"].replace("#scene-1-title", "#hero-title")
        scene["animations"][0]["target"] = "#hero-title"

        normalized = _normalize_artifact(artifact, self.media)

        self.assertIn('id="scene-1-hero-title"', normalized["scenes"][0]["html"])
        self.assertIn("#scene-1-hero-title", normalized["scenes"][0]["css"])
        self.assertEqual(normalized["scenes"][0]["animations"][0]["target"], "#scene-1-hero-title")

    def test_resolves_target_aliases_to_scene_ids_and_falls_back_locally(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["animations"][0]["target"] = "scene_1_title"
        artifact["scenes"][0]["animations"][1]["target"] = "headline"

        normalized = _normalize_artifact(artifact, self.media)
        animations = normalized["scenes"][0]["animations"]

        self.assertEqual(animations[0]["target"], "#scene-1-title")
        self.assertEqual(animations[1]["target"], "#scene-1-title")

    def test_normalizes_common_edge_zone_aliases_but_rejects_center(self) -> None:
        aliases = {
            "bottom-center": "bottom-band",
            "upper_left": "top-left",
            "左侧": "left-rail",
            "zone-right": "right-rail",
        }
        for source, expected in aliases.items():
            artifact = _artifact()
            artifact["scenes"][0]["zone"] = source
            normalized = _normalize_artifact(artifact, self.media)
            self.assertEqual(normalized["scenes"][0]["zone"], expected)

        artifact = _artifact()
        artifact["scenes"][0]["zone"] = "center"
        with self.assertRaisesRegex(ValueError, "安全区域"):
            _normalize_artifact(artifact, self.media)

    def test_clamps_finite_motion_numbers_but_rejects_non_finite_values(self) -> None:
        artifact = _artifact()
        animation = artifact["scenes"][0]["animations"][0]
        animation.update({"at": 999, "duration": 999, "from": {"x": -99_999}})
        animation["to"] = {"x": 99_999, "autoAlpha": 1, "ease": "power3.out"}

        normalized = _normalize_artifact(artifact, self.media)
        result = normalized["scenes"][0]["animations"][0]
        self.assertEqual(result["at"], 1.95)
        self.assertEqual(result["duration"], 0.05)
        self.assertEqual(result["from"]["x"], -48.0)
        self.assertEqual(result["to"]["x"], 48.0)

        artifact = _artifact()
        artifact["scenes"][0]["animations"][0]["at"] = "NaN"
        with self.assertRaisesRegex(ValueError, "有限数"):
            _normalize_artifact(artifact, self.media)

    def test_extends_or_adds_ambient_motion_before_structural_validation(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["animations"][-1]["duration"] = 0.1
        normalized = _normalize_artifact(artifact, self.media)
        ambient = [item for item in normalized["scenes"][0]["animations"] if item["kind"] == "ambient"]
        self.assertGreaterEqual(max(item["duration"] for item in ambient), 0.8)

        artifact = _artifact()
        for scene in artifact["scenes"]:
            scene["animations"] = [item for item in scene["animations"] if item["kind"] != "ambient"]
        normalized = _normalize_artifact(artifact, self.media)
        self.assertTrue(any(
            item["kind"] == "ambient"
            for item in normalized["scenes"][0]["animations"]
        ))

    def test_allows_empty_scene_css_when_html_uses_host_or_inline_styles(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["css"] = ""

        normalized = _normalize_artifact(artifact, self.media)
        composition = build_composition_html(normalized, self.media)

        self.assertEqual(normalized["scenes"][0]["css"], "")
        self.assertIn('class="hf-zone zone-top-left"', composition)

    def test_allows_gaps_but_requires_seventy_percent_coverage(self) -> None:
        artifact = _artifact()
        artifact["scenes"][1] = _scene(2, 2.4, 4.0, "bottom-right", "power2.out", "circ.out")
        normalized = _normalize_artifact(artifact, self.media)
        self.assertEqual(normalized["scenes"][1]["start"], 2.4)

        artifact = _artifact()
        artifact["scenes"] = [_scene(1, 0.0, 2.6, "top-left", "power3.out", "expo.out")]
        with self.assertRaisesRegex(ValueError, "70%"):
            _normalize_artifact(artifact, self.media)

    def test_generation_prompt_contains_opendesign_quality_contract(self) -> None:
        prompt = _build_generation_prompt(self.episode, self.media, "用户修改后的最新台词")
        self.assertIn("当前最新台词：用户修改后的最新台词", prompt)
        self.assertIn("get_context", prompt)
        self.assertIn("finalize", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("validate_semantic", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn('"designDirection": "editorial|signal|orbit|grid"', HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("禁止无脑加标点", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("必须恰好 1 个 beats", prompt)
        self.assertIn("总排队到门口？", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("完整意群", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("通总是卡壳？", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("邀请好友？", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("不得复用示例原句", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("操作员", HTML_MOTION_SYSTEM_PROMPT)

    def test_beats_render_question_and_result_pair(self) -> None:
        result = build_hyperframes_overlay(
            lambda _prompt: json.dumps({
                "designDirection": "editorial",
                "layoutRecipe": "editorial-split",
                "density": "balanced",
                "beats": [{
                    "question": "应用跳转很烦？",
                    "result": "打通所有工作！",
                }],
            }, ensure_ascii=False),
            self.episode,
            self.media,
            dialogue_text="应用跳转很烦。打通所有工作。",
            critique_enabled=False,
        )
        self.assertIn('id="scene-1-question"', result.composition_html)
        self.assertIn('id="scene-1-result"', result.composition_html)
        self.assertGreaterEqual(result.composition_html.count('class="hf-line">'), 4)
        self.assertIn('class="hf-line">打</span>', result.composition_html)
        self.assertIn('class="hf-line">通</span>', result.composition_html)
        self.assertIn("hf-density-balanced", result.composition_html)
        self.assertNotIn("hf-graphic", result.composition_html)
        self.assertIn("line-height:1.08", result.composition_html)
        self.assertIn("color:var(--motion-text)", result.composition_html)
        self.assertIn("hf-question", result.composition_html)
        self.assertIn("hf-result", result.composition_html)
        self.assertRegex(result.composition_html, r"\.hf-card-content\{[^}]*width:(?:7[0-9]|8[0-9]|9[0-2])(?:\.0)?%")

    def test_layout_recipes_produce_distinct_geometry(self) -> None:
        def build(layout: str) -> str:
            return build_hyperframes_overlay(
                lambda _prompt: json.dumps({
                    "designDirection": "editorial",
                    "layoutRecipe": layout,
                    "density": "rich",
                    "beats": [{"question": "信息总卡顿？", "result": "清晰出现！"}],
                }, ensure_ascii=False),
                self.episode,
                self.media,
                dialogue_text="关键信息总卡顿。关键信息清晰出现。",
                critique_enabled=False,
            ).composition_html

        editorial = build("editorial-split")
        signal = build("signal-frame")
        orbit = build("orbit-focus")
        grid = build("grid-brief")
        self.assertIn("hf-layout-editorial-split", editorial)
        self.assertIn("hf-layout-signal-frame", signal)
        self.assertIn("transform:rotate(-1.2deg)", orbit)
        self.assertIn("hf-layout-grid-brief", grid)
        self.assertIn("-webkit-text-stroke:0", grid)
        self.assertNotIn("align-items:center;text-align:center", signal)
        self.assertNotEqual(
            re.search(r"hf-layout-[a-z-]+", editorial).group(0),
            re.search(r"hf-layout-[a-z-]+", signal).group(0),
        )
        self.assertNotEqual(
            re.search(r"hf-layout-[a-z-]+", editorial).group(0),
            re.search(r"hf-layout-[a-z-]+", grid).group(0),
        )

    def test_semantic_contract_accepts_new_recipe_schema(self) -> None:
        semantic = {
            "designDirection": "grid",
            "layoutRecipe": "grid-brief",
            "motionRecipe": "grid-build",
            "componentRecipes": ["message-flow", "timeline-track"],
            "density": "rich",
            "anchor": "left-rail",
            "palette": {"accent": "#7DE2D1", "support": "#F4A261", "text": "#F4F1DE"},
            "beats": [
                {"question": "关键处总停顿？", "result": "开始自然流动！"},
                {"question": "跨区总卡顿？", "result": "给出结论！"},
            ],
        }
        result = build_hyperframes_overlay(
            lambda _prompt: json.dumps(semantic, ensure_ascii=False),
            self.episode,
            {**self.media, "durationSeconds": 12.0},
            dialogue_text="关键处总停顿。跨区总卡顿。信息开始自然流动。第三段给出结论。",
            critique_enabled=False,
        )

        self.assertEqual(result.summary["layoutRecipe"], "grid-brief")
        self.assertEqual(result.summary["motionRecipe"], "grid-build")
        self.assertEqual(result.summary["componentRecipes"], ["message-flow", "timeline-track"])
        self.assertEqual(result.summary["sceneCount"], 2)
        self.assertIn("hf-layout-grid-brief", result.composition_html)
        title_blocks = re.findall(
            r'id="scene-\d+-(?:question|result)"[^>]*>.*?</div>',
            result.composition_html,
            flags=re.S,
        )
        title_text = "".join(
            "".join(re.findall(r'class="hf-line(?: hf-bang)?">([^<]*)</span>', block))
            for block in title_blocks
        )
        plain = title_text.replace("？", "").replace("！", "")
        self.assertIn("关键处总停", plain)
        self.assertIn("自然流", plain)
        self.assertIn("跨区总卡", plain)
        self.assertIn("？", title_text)
        self.assertIn("！", title_text)

    def test_dazibao_headlines_keep_exclamation_or_question_mark(self) -> None:
        result = build_hyperframes_overlay(
            lambda _prompt: json.dumps({
                "designDirection": "signal",
                "layoutRecipe": "signal-frame",
                "motionRecipe": "kinetic-snap",
                "density": "balanced",
                "anchor": "top-left",
                "beats": [
                    {"question": "沟通总是卡壳？", "result": "及时接住！"},
                    {"question": "需求总卡壳？", "result": "需求接住！"},
                    {"question": "反馈总卡壳？", "result": "反馈接住！"},
                ],
            }, ensure_ascii=False),
            self.episode,
            {**self.media, "durationSeconds": 12.0, "beatIntervalSeconds": 4},
            dialogue_text="客户沟通总是卡壳。需求总卡壳。反馈总卡壳。反馈和需求都能及时接住。需求接住。反馈接住。",
            critique_enabled=False,
        )
        html = result.composition_html
        self.assertIn('class="hf-line hf-bang">？</span>', html)
        self.assertIn('class="hf-line hf-bang">！</span>', html)
        self.assertIn("痛点问答", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("错时出场", HTML_MOTION_SYSTEM_PROMPT)
        self.assertIn("邀请好友？", HTML_MOTION_SYSTEM_PROMPT)
        entrances = [
            item for item in result.artifact["scenes"][0]["animations"]
            if item.get("kind") == "entrance" and ".hf-line" in str(item.get("target") or "")
        ]
        self.assertGreaterEqual(len(entrances), 2)
        self.assertLess(float(entrances[0]["at"]), float(entrances[1]["at"]))

    def test_dense_dialogue_targets_three_quality_beats(self) -> None:
        from ai8video.media.motion.hyperframes_overlay_semantic import target_beat_count
        dialogue = (
            "做独立跨境快一年半，客户沟通总是卡壳。"
            "直到用AI8video，AI同声传译，全球聊天、支付、办公全整合。"
            "客户沟通变得零障碍，反馈和需求都能及时接住。"
            "现在邀请好友，立享返佣"
        )
        self.assertEqual(target_beat_count(19.9, dialogue), 4)
        self.assertEqual(target_beat_count(19.9, dialogue, beat_interval_seconds=10), 2)
        self.assertEqual(target_beat_count(19.9, dialogue, beat_interval_seconds=1), 20)
        self.assertEqual(target_beat_count(19.9, dialogue, beat_interval_seconds=2.2), 9)

    def test_rejects_under_count_beats_for_long_video(self) -> None:
        with self.assertRaisesRegex(ValueError, "恰好 2 个 beats"):
            build_hyperframes_overlay(
                lambda _prompt: json.dumps({
                    "designDirection": "signal",
                    "layoutRecipe": "signal-frame",
                    "motionRecipe": "kinetic-snap",
                    "density": "balanced",
                    "beats": [{"question": "在关键处停顿？", "result": "开始自然流动！"}],
                }, ensure_ascii=False),
                self.episode,
                {**self.media, "durationSeconds": 12.0},
                dialogue_text="沟通关键处停顿。跨区总卡顿。信息开始自然流动。第三段给出结论。",
                critique_enabled=False,
            )

    def test_rejects_incomplete_pair_instead_of_single_headline(self) -> None:
        with self.assertRaisesRegex(ValueError, "question（？）与 result（！）"):
            build_hyperframes_overlay(
                lambda _prompt: json.dumps({
                    "designDirection": "signal",
                    "layoutRecipe": "signal-frame",
                    "motionRecipe": "kinetic-snap",
                    "density": "balanced",
                    "anchor": "top-left",
                    "beats": [{
                        "question": "沟通总是卡壳？",
                    }],
                }, ensure_ascii=False),
                self.episode,
                self.media,
                dialogue_text="客户沟通总是卡壳。AI同声传译，全球聊天支付办公全整合。反馈和需求都能及时接住。",
                critique_enabled=False,
            )

    def test_rejects_omitted_filler_instead_of_rewriting_copy(self) -> None:
        from ai8video.media.motion.hyperframes_overlay_semantic import normalize_semantic_spec

        dialogue = (
            "做独立跨境快一年半，客户沟通总是卡壳。"
            "直到用AI8video，AI同声传译，全球聊天、支付、办公全整合。"
            "客户沟通变得零障碍，反馈和需求都能及时接住。"
        )
        with self.assertRaisesRegex(ValueError, "完整.*意群"):
            normalize_semantic_spec(
                {
                    "designDirection": "signal",
                    "layoutRecipe": "signal-frame",
                    "motionRecipe": "kinetic-snap",
                    "density": "balanced",
                    "beats": [
                        {"question": "客户沟通卡壳？", "result": "沟通变得零障碍！"},
                    ],
                },
                {**self.media, "durationSeconds": 5.0},
                dialogue_text=dialogue,
            )

    def test_rejects_duplicate_results_instead_of_rewriting_copy(self) -> None:
        from ai8video.media.motion.hyperframes_overlay_semantic import normalize_semantic_spec

        dialogue = "沟通总卡顿。跨区总卡顿。信息自然流动。结论清晰抵达。"
        with self.assertRaisesRegex(ValueError, "result 重复"):
            normalize_semantic_spec(
                {
                    "designDirection": "signal",
                    "layoutRecipe": "signal-frame",
                    "motionRecipe": "kinetic-snap",
                    "density": "balanced",
                    "beats": [
                        {"question": "沟通总卡顿？", "result": "信息自然流动！"},
                        {"question": "跨区总卡顿？", "result": "信息自然流动！"},
                    ],
                },
                {**self.media, "durationSeconds": 12.0},
                dialogue_text=dialogue,
            )

    def test_quality_retry_count_allows_invalid_plan_to_regenerate(self) -> None:
        responses = iter([
            json.dumps({"tool": "finalize", "args": {
                "beats": [{"question": "全球聊天？", "result": "办公全整合！"}],
            }}, ensure_ascii=False),
            json.dumps({"tool": "finalize", "args": {
                "beats": [{"question": "沟通总卡顿？", "result": "信息自然流动！"}],
            }}, ensure_ascii=False),
        ])
        result = build_hyperframes_overlay(
            lambda _prompt: next(responses),
            self.episode,
            {**self.media, "durationSeconds": 5.0},
            dialogue_text="沟通总卡顿。信息自然流动。",
            critique_enabled=False,
            quality_retry_count=1,
        )
        self.assertEqual(result.summary["agentTurns"], 2)

    def test_dual_level_beats_require_dialogue_source_and_global_uniqueness(self) -> None:
        dialogue = "独立跨境快一年半，客户沟通总是卡壳。AI同声传译，全球聊天、支付、办公全整合。"
        beats = [
            {"primary": "独立", "secondary": "跨境"},
            {"primary": "独立跨境", "secondary": "快一年半"},
            {"primary": "客户", "secondary": "沟通"},
            {"primary": "客户沟通", "secondary": "总是卡壳"},
            {"primary": "AI同声", "secondary": "传译"},
            {"primary": "全球", "secondary": "聊天"},
            {"primary": "办公", "secondary": "全整合"},
        ]

        def llm(prompt: str) -> str:
            self.assertIn('"chunkIndex":1', prompt)
            self.assertIn('"chunkIndex":7', prompt)
            self.assertEqual(prompt.count('"primary":"先出现的一级片段"'), 7)
            self.assertEqual(prompt.count('"secondary":"后出现的二级片段"'), 7)
            self.assertIn("primary 必须是语义上更先读的片段", prompt)
            return json.dumps({
                "audit": {"passed": True, "summary": "审核通过"},
                "semantic": {"beats": beats},
            }, ensure_ascii=False)

        result = build_hyperframes_overlay(
            llm,
            self.episode,
            {**self.media, "durationSeconds": 19.9, "beatIntervalSeconds": 3},
            dialogue_text=dialogue,
            critique_enabled=False,
        )
        self.assertEqual(len(result.semantic_spec["textBlocks"]), 7)
        self.assertTrue(all(block["role"] == "dual" for block in result.semantic_spec["textBlocks"]))
        anchors = [scene["zone"] for scene in result.artifact["scenes"][:4]]
        self.assertEqual(anchors, ["top-right", "top-left", "bottom-right", "bottom-left"])
        self.assertNotIn("font-size:.94em", result.composition_html)
        self.assertRegex(result.composition_html, r"\.hf-result\{font-size:\d+px")

    def test_dual_level_beats_allow_ai_reviewed_cross_clause_pair(self) -> None:
        from ai8video.media.motion.hyperframes_overlay_semantic import normalize_semantic_spec

        spec = normalize_semantic_spec(
            {"beats": [{"primary": "快一年半", "secondary": "客户沟通"}]},
            {**self.media, "durationSeconds": 4.0},
            dialogue_text="独立跨境快一年半，客户沟通总是卡壳。",
        )
        self.assertEqual(spec["textBlocks"][0]["role"], "dual")

    def test_dual_level_copy_quality_is_never_hard_rejected_locally(self) -> None:
        from ai8video.media.motion.hyperframes_overlay_semantic import normalize_semantic_spec

        spec = normalize_semantic_spec(
            {"beats": [{"primary": "非原台词", "secondary": "非原台词"}]},
            {**self.media, "durationSeconds": 4.0},
            dialogue_text="客户沟通总是卡壳。",
        )
        self.assertEqual(spec["textBlocks"][0]["primary"], "非原台词")
        self.assertEqual(spec["textBlocks"][0]["secondary"], "非原台词")

    def test_ai_prompt_uses_punctuation_copy_chunks(self) -> None:
        prompts: list[str] = []

        def llm(prompt: str) -> str:
            prompts.append(prompt)
            return json.dumps({
                "audit": {"passed": True, "summary": "审核通过"},
                "semantic": {"beats": [{"primary": "客户沟通", "secondary": "总是卡壳"}]},
            }, ensure_ascii=False)

        build_hyperframes_overlay(
            llm,
            self.episode,
            {**self.media, "durationSeconds": 4.0},
            dialogue_text="独立跨境快一年半，客户沟通总是卡壳。",
            critique_enabled=False,
        )
        self.assertIn('"copyChunks":[{"index":1,"text":"独立跨境快一年半","charCount":8,"needsSummary":false}', prompts[0])
        self.assertIn('{"index":2,"text":"客户沟通总是卡壳","charCount":8,"needsSummary":false}', prompts[0])
        self.assertIn("必须选择一个完整 copyChunk", prompts[0])

    def test_copy_chunks_keep_enumerations_together_unless_too_long(self) -> None:
        from ai8video.media.motion.hyperframes_overlay_agent import _dialogue_chunks

        short = _dialogue_chunks("全球聊天、支付、办公全整合。")
        self.assertEqual(short, [{
            "index": 1,
            "text": "全球聊天、支付、办公全整合",
            "charCount": 11,
            "needsSummary": False,
        }])

        long = _dialogue_chunks("全球聊天、支付办公全整合、反馈和需求都能及时接住。")
        self.assertEqual(len(long), 2)
        self.assertEqual(long[0]["text"], "全球聊天、支付办公全整合")

    def test_long_copy_chunk_is_marked_for_ai_summary(self) -> None:
        from ai8video.media.motion.hyperframes_overlay_agent import _dialogue_chunks

        chunks = _dialogue_chunks("一条关于增加带薪年假减少工作时间的建议引发职场热议。")
        self.assertTrue(chunks[0]["needsSummary"])
        self.assertGreater(chunks[0]["charCount"], 12)

    def test_smart_mode_uses_ai_planned_beat_interval(self) -> None:
        beats = [
            {"primary": f"主{index}", "secondary": f"副{index}"}
            for index in range(1, 5)
        ]

        def llm(prompt: str) -> str:
            self.assertIn("当前为智能间隔模式", prompt)
            self.assertIn('"beatIntervalSeconds":2.2', prompt)
            self.assertIn("允许一位小数", prompt)
            return json.dumps({
                "audit": {"passed": True, "summary": "节奏审核通过"},
                "semantic": {"beatIntervalSeconds": 5, "beats": beats},
            }, ensure_ascii=False)

        result = build_hyperframes_overlay(
            llm,
            self.episode,
            {
                **self.media,
                "durationSeconds": 19.9,
                "beatIntervalSeconds": 3,
                "smartBeatInterval": True,
            },
            dialogue_text="一二三四五六七八九十。",
            critique_enabled=False,
        )
        self.assertEqual(result.summary["beatIntervalSeconds"], 5)
        self.assertTrue(result.summary["smartBeatInterval"])
        self.assertEqual(len(result.artifact["scenes"]), 4)

    def test_rejects_vocabulary_fragments_as_phrase_units(self) -> None:
        with self.assertRaisesRegex(ValueError, "完整意群|碎词"):
            build_hyperframes_overlay(
                lambda _prompt: json.dumps({
                    "designDirection": "signal",
                    "layoutRecipe": "signal-frame",
                    "motionRecipe": "kinetic-snap",
                    "density": "balanced",
                    "beats": [
                        {"question": "通总是卡壳？", "result": "支付！"},
                    ],
                }, ensure_ascii=False),
                self.episode,
                self.media,
                dialogue_text=(
                    "做独立跨境快一年半，客户沟通总是卡壳。"
                    "直到用AI8video，AI同声传译，全球聊天、支付、办公全整合。"
                ),
                critique_enabled=False,
            )

    def test_rejects_mindless_cta_question_exclamation_pair(self) -> None:
        with self.assertRaisesRegex(ValueError, "CTA|痛点|空泛营销"):
            build_hyperframes_overlay(
                lambda _prompt: json.dumps({
                    "designDirection": "signal",
                    "layoutRecipe": "signal-frame",
                    "motionRecipe": "kinetic-snap",
                    "density": "balanced",
                    "anchor": "top-left",
                    "beats": [{
                        "question": "邀请好友？",
                        "result": "立享返佣！",
                    }],
                }, ensure_ascii=False),
                self.episode,
                self.media,
                dialogue_text=(
                    "做独立跨境快一年半，客户沟通总是卡壳。"
                    "直到用AI8video，AI同声传译，全球聊天、支付、办公全整合。"
                    "反馈和需求都能及时接住。"
                    "现在邀请好友，立享返佣"
                ),
                critique_enabled=False,
            )

    def test_rejects_weak_marketing_headline_instead_of_local_rewrite(self) -> None:
        with self.assertRaisesRegex(ValueError, "空泛营销"):
            build_hyperframes_overlay(
                lambda _prompt: json.dumps({
                    "designDirection": "signal",
                    "layoutRecipe": "signal-frame",
                    "motionRecipe": "kinetic-snap",
                    "density": "balanced",
                    "anchor": "top-left",
                    "beats": [{
                        "question": "沟通总卡壳？",
                        "result": "零障碍收益！",
                    }],
                }, ensure_ascii=False),
                self.episode,
                {
                    **self.media,
                    "safeZone": {"x": 8.87, "y": 3.72, "width": 84.13, "height": 43.42},
                },
                dialogue_text="跨境沟通总卡壳。全球办公全整合。需求都能及时接住。",
                critique_enabled=False,
            )

    def test_agent_tool_loop_retries_after_validate_failure(self) -> None:
        calls: list[str] = []
        bad = {
            "designDirection": "signal",
            "layoutRecipe": "signal-frame",
            "motionRecipe": "kinetic-snap",
            "density": "balanced",
            "beats": [{"question": "沟通总是卡壳？", "result": "零障碍收益！"}],
        }
        good = {
            **bad,
            "beats": [{"question": "沟通总是卡壳？", "result": "及时接住！"}],
        }

        def llm(prompt: str) -> str:
            calls.append(prompt)
            if len(calls) == 1:
                return json.dumps(bad, ensure_ascii=False)
            return json.dumps(good, ensure_ascii=False)

        result = build_hyperframes_overlay(
            llm,
            self.episode,
            self.media,
            dialogue_text="客户沟通总是卡壳。反馈和需求都能及时接住。",
            critique_enabled=False,
        )
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreaterEqual(int(result.summary.get("agentTurns") or 0), 2)
        self.assertIn("上次本地复核未通过", calls[1])
        self.assertIn('class="hf-line">及</span>', result.composition_html)
        self.assertIn('class="hf-line">住</span>', result.composition_html)

    def test_agent_returns_review_and_semantic_in_one_json(self) -> None:
        semantic = {
            "designDirection": "signal",
            "layoutRecipe": "signal-frame",
            "motionRecipe": "kinetic-snap",
            "density": "balanced",
            "beats": [{"question": "沟通总是卡壳？", "result": "及时接住！"}],
        }
        def llm(prompt: str) -> str:
            self.assertIn('"audit"', prompt)
            self.assertIn('"semantic"', prompt)
            return json.dumps({
                "audit": {"passed": True, "summary": "审核通过"},
                "semantic": semantic,
            }, ensure_ascii=False)

        result = build_hyperframes_overlay(
            llm,
            self.episode,
            self.media,
            dialogue_text="客户沟通总是卡壳。反馈和需求都能及时接住。",
            critique_enabled=False,
        )
        self.assertEqual(result.summary["harness"], "hyperframes-overlay-v5-reviewed-json")
        self.assertEqual(result.summary["agentTurns"], 1)
        self.assertEqual(result.summary["aiAudit"]["summary"], "审核通过")
        self.assertIn('class="hf-line">接</span>', result.composition_html)

    def test_vertical_stack_fits_inside_short_safe_zone(self) -> None:
        result = build_hyperframes_overlay(
            lambda _prompt: json.dumps({
                "designDirection": "signal",
                "layoutRecipe": "signal-frame",
                "motionRecipe": "kinetic-snap",
                "density": "balanced",
                "anchor": "top-left",
                "beats": [{
                    "question": "沟通总是卡壳？",
                    "result": "及时接住！",
                }],
            }, ensure_ascii=False),
            self.episode,
            {
                **self.media,
                "safeZone": {"x": 8.87, "y": 3.72, "width": 84.13, "height": 43.42},
            },
            dialogue_text="客户沟通总是卡壳。反馈和需求都能及时接住。",
            critique_enabled=False,
        )
        html = result.composition_html
        self.assertNotIn('id="scene-1-support"', html)
        self.assertIn('id="scene-1-question"', html)
        self.assertIn('id="scene-1-result"', html)
        self.assertIn('class="hf-line hf-bang">？</span>', html)
        self.assertIn('class="hf-line hf-bang">！</span>', html)
        self.assertRegex(html, r"\.hf-title\{[^}]*font-size:(?:3[2-9]|[4-8][0-9])px")

    def test_semantic_components_are_confined_to_saved_safe_zone(self) -> None:
        semantic = {
            "designDirection": "signal",
            "layoutRecipe": "signal-frame",
            "componentRecipes": ["message-flow"],
            "motionRecipe": "kinetic-snap",
            "density": "rich",
            "anchor": "top-left",
            "beats": [{"question": "信息总卡顿？", "result": "清晰出现！"}],
        }
        media = {
            **self.media,
            "safeZone": {"x": 12, "y": 14, "width": 52, "height": 30},
        }
        result = build_hyperframes_overlay(
            lambda _prompt: json.dumps(semantic, ensure_ascii=False),
            self.episode,
            media,
            dialogue_text="关键信息总卡顿。关键信息清晰出现。",
            critique_enabled=False,
        )

        # Stage is a side rail strictly inside the saved safe zone (never taller/wider than it).
        stage = re.search(r"\.hf-stage\{[^}]+\}", result.composition_html)
        self.assertIsNotNone(stage)
        self.assertIn("top:14.0%", stage.group(0))
        self.assertIn("height:30.0%", stage.group(0))
        self.assertRegex(stage.group(0), r"left:(?:12\.0|40\.0)%")
        self.assertRegex(stage.group(0), r"width:28\.0%")
        self.assertIn("hf-message-flow", result.composition_html)

    def test_semantic_contract_rejects_unplanned_dialogue_copy(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少经过提炼的文案节拍"):
            build_hyperframes_overlay(
                lambda _prompt: json.dumps({"theme": "signal"}, ensure_ascii=False),
                self.episode,
                self.media,
                dialogue_text="这是一整段没有经过规划的原始台词。",
                critique_enabled=False,
            )

    def test_scene_text_is_repaired_from_latest_dialogue(self) -> None:
        normalized = _normalize_artifact(_artifact(), self.media, "当前台词短句需要保留。后续不显示。")

        self.assertNotIn("关键沟通", normalized["scenes"][0]["html"])
        self.assertIn("当前台词短句", normalized["scenes"][0]["html"])

        without_dialogue = _normalize_artifact(_artifact(), self.media, "")
        self.assertNotIn("关键沟通", without_dialogue["scenes"][0]["html"])

    def test_model_headline_size_is_overridden_by_host_controlled_layout(self) -> None:
        artifact = _artifact()
        artifact["scenes"][0]["css"] = artifact["scenes"][0]["css"].replace("font-size:64px", "font-size:14px")

        normalized = _normalize_artifact(artifact, self.media)

        self.assertIn("font-size:14px", normalized["scenes"][0]["css"])
        composition = build_composition_html(normalized, self.media)
        self.assertIn("font-size:30px!important", composition)

    def test_pure_graphic_scene_does_not_require_headline_or_dialogue(self) -> None:
        artifact = _artifact()
        for scene in artifact["scenes"]:
            prefix = "scene-1" if scene["start"] == 0 else "scene-2"
            scene["html"] = scene["html"].replace(
                f'<h1 id="{prefix}-title" class="title">关键沟通</h1>',
                "",
            )
            scene["css"] = re.sub(rf"#{prefix}-title\{{[^}}]+\}}", "", scene["css"])
            scene["animations"] = [
                item for item in scene["animations"] if item["target"] != f"#{prefix}-title"
            ]

        normalized = _normalize_artifact(artifact, self.media, "")

        self.assertNotIn("关键沟通", normalized["scenes"][0]["html"])


if __name__ == "__main__":
    unittest.main()
