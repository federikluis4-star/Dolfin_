import sys
import unittest


sys.path.insert(0, "/Users/lev/Downloads/support-agent/dolphin-bot")

import bot  # noqa: E402


class DialogueRegressionTests(unittest.TestCase):
    def make_session(self):
        session = bot.CopilotSession(
            store="Lenovo.com",
            case_type="RNR",
            order_num="4649779458",
            customer_name="Linara Sanchez",
            customer_email="cutori01@zohomail.com",
            customer_phone="7866231392",
            details=(
                "Replacement was not received, original laptop was returned, "
                "refund still not received, case ID C004094813, 48 hours already passed."
            ),
        )
        session.latest_case_id = "C004094813"
        session.follow_up_deadline = "48 hours"
        session.follow_up_anchor_at = "2026-03-13T12:00:00-07:00"
        session.last_sent_msg = (
            "I am following up on case ID C004094813 for order 4649779458. "
            "The 48 hours window you provided has now passed."
        )
        session.transcript = [
            {
                "role": "customer_rep",
                "content": session.last_sent_msg,
                "ts": "2026-03-14T20:00:00Z",
            }
        ]
        session.message_count = 1
        return session

    def test_handoff_intro_is_not_treated_as_closing(self):
        session = self.make_session()
        msg = (
            "Kartik\nAdvisor message\n"
            "Thank you for contacting Lenovo. My name is Kartik, and I'll be glad to assist you today."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "agent_intro")
        self.assertEqual(plan["reason"], "deterministic_agent_intro")
        self.assertIn("case ID C004094813", plan["message"])
        self.assertIn("current status", plan["message"])
        self.assertNotIn("Before we end", plan["message"])

    def test_reviewing_case_reply_is_short_and_specific(self):
        session = self.make_session()
        msg = (
            "Kartik\nAdvisor message\n"
            "Let me review the order details for you. Rest assured, I will do my best to resolve this concern."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "reviewing_case")
        self.assertEqual(plan["reason"], "deterministic_reviewing_case")
        self.assertTrue(plan["message"].startswith("That's fine."))
        self.assertIn("what exact step", plan["message"])
        self.assertIn("next update deadline", plan["message"])

    def test_dropoff_claim_is_handled_as_internal_lenovo_issue(self):
        session = self.make_session()
        msg = (
            "Kartik\nAdvisor message\n"
            "After reviewing your order details, I can confirm that your refund request is currently being "
            "managed by our internal team. According to the most recent update, the package was dropped off "
            "1,405 miles away from the intended shipping address. I recommend contacting the UPS store where "
            "the package was left for further assistance."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "dropoff_location_claim")
        self.assertEqual(plan["reason"], "deterministic_dropoff_location_claim")
        self.assertIn("Lenovo's UPS label", plan["message"])
        self.assertIn("handled between Lenovo and UPS internally", plan["message"])
        self.assertIn("refund decision", plan["message"])

    def test_customer_side_case_id_is_persisted(self):
        session = bot.CopilotSession(
            store="Lenovo.com",
            case_type="RNR",
            order_num="regression-case-id-1",
            customer_name="Linara Sanchez",
            customer_email="cutori01@zohomail.com",
            customer_phone="7866231392",
            details="Refund still missing.",
        )
        session._update_case_memory(
            "I am following up on case ID C004094813 for order 4649779458.",
            role="customer_rep",
            persist=False,
        )
        self.assertEqual(session.latest_case_id, "C004094813")

    def test_real_closing_message_still_uses_closing_intent(self):
        session = self.make_session()
        msg = "Kartik\nAdvisor message\nThank you for contacting Lenovo, have a great day ahead."
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "closing_polite")
        self.assertIn("Before we end", plan["message"])


if __name__ == "__main__":
    unittest.main()
