from dataclasses import dataclass, field
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


@dataclass
class ReplayScenario:
    name: str
    description: str
    session_kwargs: dict
    agent_text: str
    expected_action: str
    expected_intent: str = ""
    expected_reason_prefix: str = ""
    required_substrings: list = field(default_factory=list)
    forbidden_substrings: list = field(default_factory=list)
    prior_agent_messages: list = field(default_factory=list)
    prior_customer_messages: list = field(default_factory=list)
    operator_notes: list = field(default_factory=list)
    latest_case_id: str = ""
    follow_up_deadline: str = ""
    follow_up_anchor_at: str = ""
    latest_case_outcome: str = ""
    last_sent_msg: str = ""


def _reset_session_state(session):
    session.latest_case_outcome = ""
    session.dialogue_state = "active_negotiation"
    session.operator_claims = []
    session.confirmed_facts = []
    session.unresolved_demands = []
    session.contradictions = []
    session.operator_notes = []
    session.pending_requested_field = ""
    session.history = []
    session.transcript = []
    session.last_agent_msg = ""
    session.last_sent_msg = ""
    session.message_count = 0


def make_session(scenario):
    session = bot.CopilotSession(**scenario.session_kwargs)
    _reset_session_state(session)
    if scenario.latest_case_id:
        session.latest_case_id = scenario.latest_case_id
    if scenario.follow_up_deadline:
        session.follow_up_deadline = scenario.follow_up_deadline
    if scenario.follow_up_anchor_at:
        session.follow_up_anchor_at = scenario.follow_up_anchor_at
    if scenario.latest_case_outcome:
        session.latest_case_outcome = scenario.latest_case_outcome
    for note in scenario.operator_notes:
        session.operator_notes.append(note)
    for content in scenario.prior_agent_messages:
        session._update_case_memory(content, role="agent", persist=False)
    for content in scenario.prior_customer_messages:
        session._update_case_memory(content, role="customer_rep", persist=False)
        session.transcript.append({"role": "customer_rep", "content": content, "ts": "2026-03-27T00:00:00Z"})
    if scenario.last_sent_msg:
        session.last_sent_msg = scenario.last_sent_msg
        session.transcript.append({"role": "customer_rep", "content": scenario.last_sent_msg, "ts": "2026-03-27T00:00:01Z"})
    session._sync_message_count_from_transcript()
    return session


def evaluate_scenario(scenario):
    session = make_session(scenario)
    plan = session.plan_next_action(agent_text=scenario.agent_text, observation={"chat_ready": True}, first_turn=False)
    failures = []
    actual_intent = session.infer_agent_intent(scenario.agent_text)

    if scenario.expected_intent and actual_intent != scenario.expected_intent:
        failures.append(f"intent={actual_intent!r} expected={scenario.expected_intent!r}")
    if scenario.expected_action and plan.get("action") != scenario.expected_action:
        failures.append(f"action={plan.get('action')!r} expected={scenario.expected_action!r}")
    if scenario.expected_reason_prefix and not (plan.get("reason") or "").startswith(scenario.expected_reason_prefix):
        failures.append(f"reason={plan.get('reason')!r} expected_prefix={scenario.expected_reason_prefix!r}")

    message = plan.get("message") or ""
    lowered = message.lower()
    for needle in scenario.required_substrings:
        if needle.lower() not in lowered:
            failures.append(f"missing={needle!r}")
    for needle in scenario.forbidden_substrings:
        if needle.lower() in lowered:
            failures.append(f"forbidden={needle!r}")

    return {
        "scenario": scenario,
        "session": session,
        "plan": plan,
        "intent": actual_intent,
        "failures": failures,
    }


SCENARIOS = [
    ReplayScenario(
        name="katrin_overdue_receipt_intro",
        description="Overdue UPS-receipt follow-up should pivot to case stage, not warehouse confirmation.",
        session_kwargs={
            "store": "Lenovo.com",
            "case_type": "RNR",
            "order_num": "4649951015",
            "customer_name": "Diana",
            "customer_email": "omeli09@zohomail.com",
            "customer_phone": "",
            "details": "UPS receipt already sent.",
        },
        latest_case_id="C003879117",
        follow_up_deadline="5-7 business days",
        follow_up_anchor_at="2026-03-14T13:49:05-07:00",
        operator_notes=["The customer already sent the UPS receipt Lenovo requested after the prior chat."],
        agent_text="Davinder\nAdvisor message\nThank you for contacting SMB Lenovo customer support! My name is Davinder, and I'll be assisting you today.",
        expected_action="send_message",
        expected_intent="agent_intro",
        expected_reason_prefix="deterministic_agent_intro",
        required_substrings=["UPS receipt Lenovo requested", "current stage", "still pending", "final refund decision"],
        forbidden_substrings=["written confirmation from your warehouse"],
    ),
    ReplayScenario(
        name="katrin_wait_preamble",
        description="Thank-you preambles should wait for the substantive Lenovo update.",
        session_kwargs={
            "store": "Lenovo.com",
            "case_type": "RNR",
            "order_num": "4649951015",
            "customer_name": "Diana",
            "customer_email": "omeli09@zohomail.com",
            "customer_phone": "",
            "details": "UPS receipt already sent.",
        },
        latest_case_id="C003879117",
        follow_up_deadline="5-7 business days",
        follow_up_anchor_at="2026-03-14T13:49:05-07:00",
        operator_notes=["The customer already sent the UPS receipt Lenovo requested after the prior chat."],
        agent_text="Davinder\nAdvisor message\nThank you for waiting.",
        expected_action="wait",
        expected_intent="status_update_preamble",
        expected_reason_prefix="wait_for_substantive_",
    ),
    ReplayScenario(
        name="katrin_delivery_vs_inspection_empathy",
        description="After delivery vs inspection conflict, generic empathy should trigger contradiction pressure.",
        session_kwargs={
            "store": "Lenovo.com",
            "case_type": "RNR",
            "order_num": "4649951015",
            "customer_name": "Diana",
            "customer_email": "omeli09@zohomail.com",
            "customer_phone": "",
            "details": "UPS receipt already sent.",
        },
        latest_case_id="C003879117",
        prior_agent_messages=[
            "According to the tracking information, it appears that the package may have been returned to the warehouse.",
            "However, the warehouse team has confirmed that they have not been able to find the unit upon checking the package.",
        ],
        agent_text="Davinder\nAdvisor message\nI understand your concern and I appreciate your time.",
        expected_action="send_message",
        expected_intent="generic_empathy",
        expected_reason_prefix="deterministic_generic_empathy",
        required_substrings=["inconsistent", "review", "final refund decision"],
        forbidden_substrings=["general assurance"],
    ),
    ReplayScenario(
        name="luna_policy_confidential_empathy",
        description="Late denial should persist across later generic empathy turns.",
        session_kwargs={
            "store": "Lenovo.com",
            "case_type": "RNR",
            "order_num": "4649779458",
            "customer_name": "Linara Sanchez",
            "customer_email": "cutori01@zohomail.com",
            "customer_phone": "7866231392",
            "details": "Refund still missing.",
        },
        latest_case_id="C004094813",
        prior_agent_messages=[
            "The lost case was not approved.",
            "Please note that the policy being followed is internal and confidential, so we are unable to share it.",
        ],
        agent_text="Anmol\nAdvisor message\nI understand your concern and I appreciate your time.",
        expected_action="send_message",
        expected_intent="generic_empathy",
        expected_reason_prefix="deterministic_generic_empathy",
        required_substrings=["non-confidential", "denial basis", "under review"],
    ),
    ReplayScenario(
        name="luna_closure_attempt_ack",
        description="Closure attempts after denial should stay on final-position handling even on a later thank-you.",
        session_kwargs={
            "store": "Lenovo.com",
            "case_type": "RNR",
            "order_num": "4649779458",
            "customer_name": "Linara Sanchez",
            "customer_email": "cutori01@zohomail.com",
            "customer_phone": "7866231392",
            "details": "Refund still missing.",
        },
        latest_case_id="C004094813",
        prior_agent_messages=[
            "Your case was investigated by the NA Case Managers. Refunds are withheld if the investigation finds no return or loss confirmation.",
            "As per policy, please note that the policy being followed is internal and confidential, so we are unable to share it. If you have no other questions, we will proceed and close this conversation.",
        ],
        agent_text="Anmol\nAdvisor message\nThank you.",
        expected_action="send_message",
        expected_intent="acknowledgement_only",
        expected_reason_prefix="deterministic_acknowledgement_only",
        required_substrings=["not agreeing to close", "closed or still under review", "final denial basis"],
    ),
]
