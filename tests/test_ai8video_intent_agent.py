from __future__ import annotations

import unittest

from ai8video.application.intent_agent import IntentAgent, IntentContext


class IntentAgentTest(unittest.TestCase):
    def test_replan_is_always_current_confirmation_followup(self) -> None:
        agent = IntentAgent()

        decision = agent.decide(
            "重新分集：5 集，要偷偷地为向飞讯打广告",
            IntentContext(awaiting="smart_split_confirmation", completed_runs=0),
        )

        self.assertEqual(decision.route, "smart_split_followup")
        self.assertFalse(decision.reset_session)

    def test_completed_generation_request_starts_new_session(self) -> None:
        agent = IntentAgent(lambda _: '{"intent":"generation","confidence":0.9}')

        decision = agent.decide("重新做一套 Temu 入门视频", IntentContext(completed_runs=1))

        self.assertEqual(decision.route, "new_request")
        self.assertTrue(decision.reset_session)

    def test_confirmation_followup_keeps_model_interpretation(self) -> None:
        calls: list[str] = []

        def llm(prompt: str) -> str:
            calls.append(prompt)
            return '{"intent":"smart_split_replan","video_count":6,"style_hint":"林默改名林妹","confidence":0.99}'

        decision = IntentAgent(llm).decide(
            "重新分集：6 集，林默改名林妹",
            IntentContext(awaiting="smart_split_confirmation"),
        )

        self.assertEqual(decision.route, "smart_split_followup")
        self.assertEqual(decision.interpretation["video_count"], 6)
        self.assertEqual(decision.interpretation["style_hint"], "林默改名林妹")
        self.assertEqual(len(calls), 1)

    def test_completed_rewrite_keeps_current_session(self) -> None:
        agent = IntentAgent(lambda _: '{"intent":"rewrite","confidence":0.9}')

        decision = agent.decide("第二条改得更真实", IntentContext(completed_runs=1))

        self.assertEqual(decision.route, "rewrite")
        self.assertFalse(decision.reset_session)


if __name__ == "__main__":
    unittest.main()
