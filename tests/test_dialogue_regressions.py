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
        session.operator_notes = []
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

    def test_acknowledgement_only_stays_short(self):
        session = self.make_session()
        msg = "Davinder\nAdvisor message\nThank you."
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "acknowledgement_only")
        self.assertEqual(plan["reason"], "deterministic_acknowledgement_only")
        self.assertTrue(plan["message"].startswith("Understood."))
        self.assertNotIn("team or person", plan["message"])

    def test_information_gathering_delay_does_not_repeat_policy_bundle(self):
        session = self.make_session()
        session.transcript.extend([
            {
                "role": "customer_rep",
                "content": "Please provide the exact written policy Lenovo relies on to withhold my refund, the team handling the approval, and the firm deadline.",
                "ts": "2026-03-14T20:01:00Z",
            },
            {
                "role": "customer_rep",
                "content": "Please provide the exact written policy Lenovo relies on to withhold my refund, the team handling the approval, and the firm deadline.",
                "ts": "2026-03-14T20:02:00Z",
            },
        ])
        session._sync_message_count_from_transcript()
        msg = (
            "Davinder\nAdvisor message\n"
            "I previously requested that you allow me a moment to retrieve the necessary details. "
            "I appreciate your patience while I gather the information for you."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "information_gathering_delay")
        self.assertEqual(plan["reason"], "deterministic_information_gathering_delay")
        self.assertIn("actual review result", plan["message"])
        self.assertNotIn("exact written policy Lenovo relies on", plan["message"])

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

    def test_dropoff_claim_uses_travel_explanation_when_case_context_has_it(self):
        session = self.make_session()
        session.operator_notes.append(
            "Customer was away visiting parents when the return was dropped off, so the drop-off location was away from the home address. The return used the Lenovo-issued UPS label."
        )
        msg = (
            "Kartik\nAdvisor message\n"
            "The package was dropped off 1,405 miles away from the shipping address. Please contact the UPS store."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "dropoff_location_claim")
        self.assertIn("visiting my parents", plan["message"])
        self.assertIn("Lenovo's UPS label", plan["message"])

    def test_case_canceled_redirect_is_not_treated_as_generic_policy_bundle(self):
        session = self.make_session()
        msg = (
            "Davinder\nAdvisor message\n"
            "The team canceled the case and suggested that the customer should contact the UPS store and "
            "contact local authorities regarding this, since the product was not returned to the warehouse."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "case_canceled_ups_redirect")
        self.assertEqual(plan["reason"], "deterministic_case_canceled_ups_redirect")
        self.assertIn("Canceling Lenovo's internal case does not transfer", plan["message"])
        self.assertIn("reopen or re-escalate the case", plan["message"])

    def test_missing_product_after_delivery_claim_is_classified_as_inspection_issue(self):
        session = self.make_session()
        msg = (
            "Davinder\nAdvisor message\n"
            "I acknowledge that the tracking information indicates the package was returned to the warehouse. "
            "However, upon inspection, it was discovered that the product itself was not inside the returned package."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "missing_product_after_delivery_claim")
        self.assertEqual(plan["reason"], "deterministic_missing_product_after_delivery_claim")
        self.assertIn("empty-box or tampering claim", plan["message"])
        self.assertIn("final refund decision", plan["message"])

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
