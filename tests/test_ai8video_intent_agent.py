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

    def test_completed_rewrite_keeps_current_session(self) -> None:
        agent = IntentAgent(lambda _: '{"intent":"rewrite","confidence":0.9}')

        decision = agent.decide("第二条改得更真实", IntentContext(completed_runs=1))

        self.assertEqual(decision.route, "rewrite")
        self.assertFalse(decision.reset_session)


if __name__ == "__main__":
    unittest.main()
