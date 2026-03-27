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
        session.latest_case_outcome = ""
        session.dialogue_state = "active_negotiation"
        session.operator_claims = []
        session.confirmed_facts = []
        session.unresolved_demands = []
        session.contradictions = []
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

    def test_status_update_preamble_waits_for_substantive_reply(self):
        session = self.make_session()
        msg = "Davinder\nAdvisor message\nThank you for waiting."
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "status_update_preamble")
        self.assertEqual(plan["action"], "wait")
        self.assertEqual(plan["message"], "")
        self.assertIn("wait_for_substantive", plan["reason"])

    def test_courtesy_greeting_preamble_waits_instead_of_repeating_demands(self):
        session = self.make_session()
        msg = "Advisor message\nHope you are doing well!🙂"
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "courtesy_greeting_preamble")
        self.assertEqual(plan["action"], "wait")
        self.assertEqual(plan["message"], "")

    def test_merged_lenovo_consumer_question_still_classifies_correctly(self):
        session = self.make_session()
        msg = "LenovoAdvisor messageAre you a retail consumer or a small business? 8 mins ago"
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "consumer_type_question")
        self.assertEqual(plan["reason"], "deterministic_consumer_type_question")
        self.assertEqual(plan["message"], "I am a retail consumer.")

    def test_normalize_live_chat_message_ignores_lenovo_wrapper_and_relative_time(self):
        clean = "Advisor message\nAre you a retail consumer or a small business?"
        noisy = "LenovoAdvisor messageAre you a retail consumer or a small business? 14 mins ago"
        self.assertEqual(bot.normalize_live_chat_message(clean), bot.normalize_live_chat_message(noisy))

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

    def test_timed_check_details_request_is_treated_as_short_hold_request(self):
        session = self.make_session()
        msg = "Davinder\nAdvisor message\nDiana, please allow me 2 minutes to check the details."
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "hold_request")
        self.assertEqual(plan["reason"], "deterministic_hold_request")
        self.assertTrue(plan["message"].startswith("Yes, that's fine."))
        self.assertNotIn("warehouse", plan["message"].lower())
        self.assertNotIn("escalation owner", plan["message"].lower())

    def test_overdue_receipt_case_switches_to_case_stage_follow_up(self):
        session = self.make_session()
        session.order_num = "4649951015"
        session.latest_case_id = "C003879117"
        session.follow_up_deadline = "5-7 business days"
        session.follow_up_anchor_at = "2026-03-14T13:49:05-07:00"
        session.details = "UPS receipt already sent."
        session.operator_notes.append("The customer already sent the UPS receipt Lenovo requested after the prior chat.")
        msg = "Davinder\nAdvisor message\nThank you for contacting SMB Lenovo customer support! My name is Davinder, and I'll be assisting you today."
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "agent_intro")
        self.assertEqual(plan["reason"], "deterministic_agent_intro")
        self.assertIn("UPS receipt Lenovo requested", plan["message"])
        self.assertIn("current stage", plan["message"])
        self.assertIn("still pending", plan["message"])
        self.assertIn("final refund decision", plan["message"])
        self.assertNotIn("written confirmation from your warehouse", plan["message"])

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

    def test_missing_unit_upon_checking_package_is_also_classified_as_inspection_issue(self):
        session = self.make_session()
        msg = (
            "Davinder\nAdvisor message\n"
            "According to the tracking information, it appears that the package may have been returned to the warehouse. "
            "However, the warehouse team has confirmed that they have not been able to find the unit upon checking the package."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "missing_product_after_delivery_claim")
        self.assertEqual(plan["reason"], "deterministic_missing_product_after_delivery_claim")
        self.assertIn("empty-box or tampering claim", plan["message"])

    def test_generic_empathy_after_delivery_vs_warehouse_conflict_forces_contradiction_follow_up(self):
        session = self.make_session()
        session._update_case_memory(
            "According to the tracking information, it appears that the package may have been returned to the warehouse.",
            role="agent",
            persist=False,
        )
        session._update_case_memory(
            "The warehouse team has confirmed that they have not been able to find the unit upon checking the package.",
            role="agent",
            persist=False,
        )
        msg = (
            "Davinder\nAdvisor message\n"
            "I understand your concern and I appreciate your time."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "generic_empathy")
        self.assertEqual(plan["reason"], "deterministic_generic_empathy")
        self.assertIn("inconsistent", plan["message"].lower())
        self.assertIn("review", plan["message"].lower())
        self.assertIn("final refund decision", plan["message"].lower())

    def test_contradiction_follow_up_asks_prioritize_gap_review_type_and_decision_date(self):
        session = self.make_session()
        session._update_case_memory(
            "According to the tracking information, it appears that the package may have been returned to the warehouse.",
            role="agent",
            persist=False,
        )
        session._update_case_memory(
            "The warehouse has not received the returned item.",
            role="agent",
            persist=False,
        )
        asks = session.next_best_asks("Advisor message\nPlease be assured that I am dedicated to supporting you throughout this process.")
        joined = " | ".join(asks).lower()
        self.assertIn("carrier delivery record", joined)
        self.assertIn("review", joined)
        self.assertIn("final refund decision", joined)

    def test_lost_case_denied_requests_final_denial_summary_and_case_status(self):
        session = self.make_session()
        msg = (
            "Anmol\nAdvisor message\n"
            "The lost case was not approved."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "lost_case_denied")
        self.assertEqual(plan["reason"], "deterministic_lost_case_denied")
        self.assertIn("final denial", plan["message"])
        self.assertIn("denial basis", plan["message"])
        self.assertTrue("closed" in plan["message"] or "under review" in plan["message"])

    def test_agent_intro_after_late_denial_keeps_final_denial_focus(self):
        session = self.make_session()
        session._update_case_memory("The lost case was not approved.", role="agent", persist=False)
        session._update_case_memory(
            "Please note that the policy being followed is internal and confidential, so we are unable to share it.",
            role="agent",
            persist=False,
        )
        msg = (
            "Davinder\nAdvisor message\n"
            "Thank you for contacting SMB Lenovo customer support! My name is Davinder, and I'll be assisting you today."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "agent_intro")
        self.assertEqual(plan["reason"], "deterministic_agent_intro")
        self.assertIn("non-confidential", plan["message"])
        self.assertIn("denial basis", plan["message"])
        self.assertTrue("closed" in plan["message"] or "under review" in plan["message"])

    def test_internal_policy_confidential_requests_nonconfidential_summary_and_owner(self):
        session = self.make_session()
        msg = (
            "Anmol\nAdvisor message\n"
            "Please note that the policy being followed is internal and confidential, so we are unable to share it."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "internal_policy_confidential")
        self.assertEqual(plan["reason"], "deterministic_internal_policy_confidential")
        self.assertIn("non-confidential", plan["message"])
        self.assertIn("denial basis", plan["message"])
        self.assertTrue("owner" in plan["message"] or "team" in plan["message"])

    def test_generic_empathy_after_policy_confidential_keeps_late_denial_state(self):
        session = self.make_session()
        session._update_case_memory("The lost case was not approved.", role="agent", persist=False)
        session._update_case_memory(
            "Please note that the policy being followed is internal and confidential, so we are unable to share it.",
            role="agent",
            persist=False,
        )
        msg = (
            "Anmol\nAdvisor message\n"
            "I understand your concern and I appreciate your time."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "generic_empathy")
        self.assertEqual(plan["reason"], "deterministic_generic_empathy")
        self.assertIn("non-confidential", plan["message"])
        self.assertIn("denial basis", plan["message"])
        self.assertTrue("closed" in plan["message"] or "under review" in plan["message"])

    def test_denial_basis_summary_is_locked_instead_of_repeating_policy_bundle(self):
        session = self.make_session()
        msg = (
            "Anmol\nAdvisor message\n"
            "Your case was investigated by the NA Case Managers. Refunds are withheld if the investigation finds "
            "no return or loss confirmation. The official policy is that refunds are only issued once the return "
            "is verified or loss is confirmed by Lenovo and the carrier."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "denial_basis_summary_provided")
        self.assertEqual(plan["reason"], "deterministic_denial_basis_summary_provided")
        self.assertIn("final denial basis", plan["message"])
        self.assertTrue("closed" in plan["message"] or "under review" in plan["message"])
        self.assertTrue("owner" in plan["message"] or "team" in plan["message"])

    def test_closure_attempt_after_denial_survives_generic_follow_up(self):
        session = self.make_session()
        session._update_case_memory(
            "Your case was investigated by the NA Case Managers. Refunds are withheld if the investigation finds no return or loss confirmation.",
            role="agent",
            persist=False,
        )
        session._update_case_memory(
            "As per policy, please note that the policy being followed is internal and confidential, so we are unable to share it. If you have no other questions, we will proceed and close this conversation.",
            role="agent",
            persist=False,
        )
        msg = "Anmol\nAdvisor message\nThank you."
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "acknowledgement_only")
        self.assertEqual(plan["reason"], "deterministic_acknowledgement_only")
        self.assertIn("not agreeing to close", plan["message"])
        self.assertTrue("closed" in plan["message"] or "under review" in plan["message"])

    def test_closure_after_denial_rejects_case_closure_without_final_position(self):
        session = self.make_session()
        msg = (
            "Anmol\nAdvisor message\n"
            "As per policy, the policy being followed is internal and confidential, so we are unable to share it. "
            "If you have no other questions, we will proceed and close this conversation."
        )
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "closure_after_denial")
        self.assertEqual(plan["reason"], "deterministic_closure_after_denial")
        self.assertIn("not agreeing to close", plan["message"])
        self.assertTrue("closed" in plan["message"] or "under review" in plan["message"])

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

    def test_customer_side_ups_receipt_note_is_persisted(self):
        session = self.make_session()
        session._update_case_memory(
            "Your colleague asked me for a UPS receipt, and I already provided the UPS receipt Lenovo requested.",
            role="customer_rep",
            persist=False,
        )
        self.assertTrue(session._has_submitted_ups_receipt())
        self.assertTrue(any("UPS receipt" in note for note in session.operator_notes))

    def test_real_closing_message_still_uses_closing_intent(self):
        session = self.make_session()
        msg = "Kartik\nAdvisor message\nThank you for contacting Lenovo, have a great day ahead."
        plan = session.plan_next_action(agent_text=msg, observation={"chat_ready": True}, first_turn=False)
        self.assertEqual(session.infer_agent_intent(msg), "closing_polite")
        self.assertIn("Before we end", plan["message"])


if __name__ == "__main__":
    unittest.main()
