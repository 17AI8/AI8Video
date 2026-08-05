from __future__ import annotations

import unittest

from ai8video.agent_runtime import AgentRunContext, CapabilityRegistry, CapabilitySpec
from ai8video.agent_runtime.planning_capability import (
    PLANNING_CAPABILITY_NAME,
    PlanningCapabilityInput,
    build_planning_capability,
)
from ai8video.core.models import ParsedRequest, VideoPrompt


class CapabilityRegistryTest(unittest.TestCase):
    def test_emits_lifecycle_events_and_validates_types(self) -> None:
        events: list[dict] = []
        registry = CapabilityRegistry()
        registry.register(CapabilitySpec(
            name="test.echo",
            agent_id="test-agent",
            description="回显",
            handler=lambda _context, value: value.upper(),
            input_type=str,
            output_type=str,
        ))

        result = registry.execute(
            "test.echo",
            AgentRunContext(session_id="session-1", event_sink=events.append),
            "hello",
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.value, "HELLO")
        self.assertEqual([item["event"] for item in events], ["capability_start", "capability_end"])
        with self.assertRaises(TypeError):
            registry.execute("test.echo", AgentRunContext(), 123)

    def test_cancellation_blocks_handler_before_side_effect(self) -> None:
        called = False

        def handler(_context, value):
            nonlocal called
            called = True
            return value

        registry = CapabilityRegistry()
        registry.register(CapabilitySpec(
            name="test.cancel",
            agent_id="test-agent",
            description="取消测试",
            handler=handler,
            input_type=str,
            output_type=str,
            side_effects=True,
            replay_safe=False,
        ))

        with self.assertRaisesRegex(RuntimeError, "已取消"):
            registry.execute(
                "test.cancel",
                AgentRunContext(cancel_check=lambda: True),
                "payload",
            )
        self.assertFalse(called)

    def test_side_effect_capability_cannot_opt_into_parallel_execution(self) -> None:
        registry = CapabilityRegistry()
        with self.assertRaisesRegex(ValueError, "必须串行"):
            registry.register(CapabilitySpec(
                name="test.unsafe-parallel",
                agent_id="test-agent",
                description="非法并行副作用",
                handler=lambda _context, value: value,
                input_type=str,
                output_type=str,
                side_effects=True,
                execution_mode="parallel",
            ))

    def test_planning_capability_preserves_existing_domain_model(self) -> None:
        request = ParsedRequest(raw_text="生成一条视频", mode="single_video")
        capability = build_planning_capability(
            infer_count=lambda *_args, **_kwargs: (1, "unused"),
            smart_plan=lambda *_args, **_kwargs: [],
            repeat_plan=lambda *_args, **_kwargs: [],
            single_plan=lambda *_args, **_kwargs: [
                VideoPrompt(index=1, title="单条视频", prompt="生成一条视频")
            ],
        )
        registry = CapabilityRegistry()
        registry.register(capability)

        result = registry.execute(
            PLANNING_CAPABILITY_NAME,
            AgentRunContext(session_id="planner-session"),
            PlanningCapabilityInput(
                request=request,
                target_duration=10,
                task_constraints="",
                smart_split=False,
                allow_mock=True,
                llm=None,
                trace_session_id="planner-session",
            ),
        )

        self.assertEqual(result.value[0].prompt, "生成一条视频")
        self.assertEqual(capability.policy_skills, ("plan-video-content",))
        self.assertFalse(capability.side_effects)
        self.assertEqual(capability.execution_mode, "parallel")

    def test_planning_capability_uses_ai_plan_with_locked_smart_count(self) -> None:
        request = ParsedRequest(raw_text="重新分集为 6 条", mode="batch_videos", video_count=6)
        calls = {"infer": 0, "smart": 0, "repeat": 0}

        def infer_count(*_args, **_kwargs):
            calls["infer"] += 1
            return 1, "不应调用"

        def smart_plan(*_args, **_kwargs):
            calls["smart"] += 1
            return [VideoPrompt(index=index, title=f"主题 {index}", prompt=f"方案 {index}") for index in range(1, 7)]

        capability = build_planning_capability(
            infer_count=infer_count,
            smart_plan=smart_plan,
            repeat_plan=lambda *_args, **_kwargs: calls.__setitem__("repeat", calls["repeat"] + 1) or [],
            single_plan=lambda *_args, **_kwargs: [],
        )
        registry = CapabilityRegistry()
        registry.register(capability)

        result = registry.execute(
            PLANNING_CAPABILITY_NAME,
            AgentRunContext(session_id="locked-smart-count"),
            PlanningCapabilityInput(
                request=request,
                target_duration=10,
                task_constraints="",
                smart_split=True,
                allow_mock=True,
                llm=None,
                trace_session_id="locked-smart-count",
                smart_split_count_locked=True,
            ),
        )

        self.assertEqual(len(result.value), 6)
        self.assertEqual(calls, {"infer": 0, "smart": 1, "repeat": 0})

    def test_parallel_episode_strategy_is_explicit_and_legacy_remains_default(self) -> None:
        request = ParsedRequest(
            raw_text="智能分集素材",
            mode="batch_videos",
            video_count=2,
            tail_frame_chaining=True,
        )
        calls = {"legacy": 0, "parallel": 0}
        parallel_kwargs: dict = {}

        def legacy_plan(*_args, **_kwargs):
            calls["legacy"] += 1
            return [VideoPrompt(index=1, title="旧路径", prompt="旧路径")]

        def parallel_plan(*_args, **_kwargs):
            calls["parallel"] += 1
            parallel_kwargs.update(_kwargs)
            return [VideoPrompt(index=1, title="并发路径", prompt="并发路径")]

        capability = build_planning_capability(
            infer_count=lambda *_args, **_kwargs: (2, "unused"),
            smart_plan=legacy_plan,
            parallel_smart_plan=parallel_plan,
            repeat_plan=lambda *_args, **_kwargs: [],
            single_plan=lambda *_args, **_kwargs: [],
        )
        registry = CapabilityRegistry()
        registry.register(capability)

        legacy_result = registry.execute(
            PLANNING_CAPABILITY_NAME,
            AgentRunContext(),
            PlanningCapabilityInput(
                request=request,
                target_duration=10,
                task_constraints="",
                smart_split=True,
                smart_split_count_locked=True,
                allow_mock=True,
                llm=None,
                trace_session_id=None,
            ),
        )
        parallel_result = registry.execute(
            PLANNING_CAPABILITY_NAME,
            AgentRunContext(),
            PlanningCapabilityInput(
                request=request,
                target_duration=10,
                task_constraints="",
                smart_split=True,
                smart_split_count_locked=True,
                use_parallel_episode_planning=True,
                allow_mock=True,
                llm=None,
                trace_session_id=None,
            ),
        )

        self.assertEqual(legacy_result.value[0].title, "旧路径")
        self.assertEqual(parallel_result.value[0].title, "并发路径")
        self.assertEqual(calls, {"legacy": 1, "parallel": 1})
        self.assertTrue(parallel_kwargs["tail_frame_chaining"])


if __name__ == "__main__":
    unittest.main()
