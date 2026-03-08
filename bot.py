#!/usr/bin/env python3
"""
Support Copilot — Dolphin Anty Bot
Подключается к профилю Dolphin Anty через Local API,
читает чат поддержки и генерирует юридические ответы через OpenRouter.
"""

import asyncio
import json
import sys
import os
import select
import time
import re
import random
import difflib
from pathlib import Path
import requests
from datetime import datetime
from playwright.async_api import async_playwright

# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

def load_env_file(env_path=".env"):
    """Простая загрузка KEY=VALUE из .env без внешних зависимостей."""
    try:
        path = Path(env_path)
        if not path.exists():
            return
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass

load_env_file()

DOLPHIN_API = "http://localhost:3001/v1.0"   # Dolphin Anty Local API (стандартный порт)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DOLPHIN_SESSION_TOKEN = os.getenv("DOLPHIN_SESSION_TOKEN", "")
DOLPHIN_CLOUD_API_KEY = os.getenv("DOLPHIN_CLOUD_API_KEY", "")
DEFAULT_CUSTOMER_EMAIL = os.getenv("DEFAULT_CUSTOMER_EMAIL", "")
DEFAULT_CUSTOMER_NAME = os.getenv("DEFAULT_CUSTOMER_NAME", "")
DEFAULT_CUSTOMER_PHONE = os.getenv("DEFAULT_CUSTOMER_PHONE", "")
DEFAULT_ORDER_NUM = os.getenv("DEFAULT_ORDER_NUM", "")
LENOVO_CHAT_URL = os.getenv(
    "LENOVO_CHAT_URL",
    "https://www.lenovo.com/us/vipmembers/ticketsatwork/en/contact/order-support/",
)
# 1 (default): не закрывать профиль при выходе бота.
# 0: закрывать профиль в finally.
KEEP_PROFILE_OPEN = os.getenv("KEEP_PROFILE_OPEN", "1") != "0"
# 1: разрешить старт профиля через POST /browser_profiles/start (может создать temporary profileId).
# 0 (default): не использовать этот путь.
ALLOW_TEMP_PROFILE_START = os.getenv("ALLOW_TEMP_PROFILE_START", "0") == "1"
# 1: разрешить stop/start уже запущенного профиля для получения automation-порта.
# 0 (default): не перезапускать уже открытый профиль.
_ALLOW_PROFILE_RESTART_RAW = os.getenv("ALLOW_PROFILE_RESTART", "0") == "1"
# По умолчанию принудительно запрещаем restart профиля, чтобы окно не закрывалось/открывалось.
# Для отладки можно снять защиту: STRICT_KEEP_PROFILE=0 и ALLOW_PROFILE_RESTART=1
STRICT_KEEP_PROFILE = os.getenv("STRICT_KEEP_PROFILE", "1") != "0"
ALLOW_PROFILE_RESTART = _ALLOW_PROFILE_RESTART_RAW and (not STRICT_KEEP_PROFILE)

SYSTEM_PROMPT = """You are an elite US consumer-rights negotiation copilot writing live support-chat messages for a customer.

PRIMARY GOAL:
- Drive the case to a concrete positive resolution as fast as possible:
  1) Full refund, or
  2) Free replacement/reshipment, or
  3) Confirmed supervisor/escalations ticket with deadline.

OUTPUT RULES:
- Output ONLY the next message to send to the support agent.
- English only.
- 2-5 short sentences, direct and professional.
- End with one clear requested action and timeline.
- Write to the support agent, not to the customer.
- Never greet or address the customer by name.
- Never ask the customer to choose a remedy; ask the support agent to confirm the available resolution.

QUALITY RULES:
- Write in natural, grammatical US English.
- Fix grammar, spelling, punctuation, capitalization, and phrasing before finalizing the message.
- Avoid broken English, literal translations, slang, filler, repeated words, and robotic wording.
- Use simple business-chat language that sounds like a competent human representative.
- Use only facts provided in the case/chat history. Do not invent facts, policies, or evidence.
- Do not claim to be a lawyer; write as a customer representative.
- Start cooperative, then become firm if delayed/denied.
- Keep pressure legal and realistic (FCBA/FTC/chargeback) only when needed.
- If agent asks for missing data, provide concise compliance and restate the resolution request.

NEGOTIATION PLAYBOOK:
1) Align + summarize issue + ask for specific remedy.
2) If stalling: ask for supervisor/escalations team + case ID.
3) If denial without basis: challenge politely, request policy citation in writing.
4) If unresolved: set final deadline (48h or 5 business days) and mention formal dispute path.

SUCCESS CRITERIA FOR EACH MESSAGE:
- Moves conversation one step closer to refund/replacement/escalation confirmation.
- Contains a concrete next action from agent.
- Avoids emotional or vague language.
- Reads as if it was proofread by a fluent English speaker before sending."""

AGENT_DECISION_PROMPT = """You are the decision engine for a live e-commerce support-chat agent.

Return JSON only. No markdown. No explanations outside JSON.

Your job:
- Read the current case context, transcript, latest agent message, and UI observation.
- Decide the single best next action.
- Prefer concrete progress over generic replies.
- Respond to the latest operator move, not to the case in general.
- Avoid repeating the same demand unless the operator ignored it and the message explicitly says that.
- If the operator asks a narrow question or makes a specific claim, answer that exact point first, then push the case forward.
- If the chat is not truly ready for a live agent message, choose wait.
- If the agent asks for data already known in the case, answer directly and concisely.
- If the agent is vague or stalling, ask for a concrete action, case ID, escalation owner, or timeline.
- Write in natural, grammatical US English.
- Write to the support agent, not to the customer.
- You are the customer's representative speaking to the merchant's support agent.
- Never address the customer by name.
- Never ask the customer what they prefer; ask the support agent to confirm what they can do.
- Never speak as the merchant, support team, refunds team, or an internal department.
- Never say you will check, review internally, contact another team, or provide an internal update later.
- Never ask the support agent to hold while you verify information.

Allowed actions:
- send_message
- wait
- finish

JSON schema:
{
  "action": "send_message|wait|finish",
  "message": "string",
  "goal": "string",
  "reason": "string",
  "confidence": 0.0
}

Rules:
- `message` must be empty when action is wait or finish.
- Never invent facts, policies, evidence, or promises.
- Keep message to 2-5 short sentences.
- Do not repeat the previous customer-side message with light rewording.
- End the message with one clear requested action and timeline when action is send_message."""

# ─── ПАРСИНГ ИМЕНИ ПРОФИЛЯ ───────────────────────────────────────────────────
# Формат: "ИмяКлиента_Магазин" или "ИмяКлиента_Магазин_Тип"
# Примеры: Luna_CA, Mike_Lenovo_RNR, John_Amazon_INR, Sara_Zara

def parse_profile_name(name):
    """
    Извлекает клиента, магазин и тип кейса из имени профиля.
    Формат: ИмяКлиента_Магазин [_ТипКейса]
    Примеры: Luna_CA → клиент=Luna, магазин=CA(неизвестен)
             Mike_Lenovo → клиент=Mike, магазин=Lenovo.com
             Sara_Amazon_INR → клиент=Sara, магазин=Amazon, кейс=INR
    """
    parts = name.strip().split("_")
    result = {"client": parts[0] if parts else name, "store": None, "case_type": None}

    store_map = {
        "amazon": "Amazon", "amz": "Amazon",
        "lenovo": "Lenovo.com", "len": "Lenovo.com", "lnv": "Lenovo.com",
        "zara": "Zara.com",
        "walmart": "Walmart", "wm": "Walmart",
        "ebay": "eBay",
        "ca": "Lenovo.com",  # на основе скрина "Luna_CA" — это Lenovo аккаунт
    }
    case_map = {"inr": "INR", "rnr": "RNR"}

    for part in parts[1:]:
        p = part.lower()
        if p in store_map:
            result["store"] = store_map[p]
        elif p in case_map:
            result["case_type"] = case_map[p]

    return result


# ─── СЕЛЕКТОРЫ ЧАТА ──────────────────────────────────────────────────────────
# Lenovo использует Genesys Web Widget
# Селекторы получены из реального скриншота чата lenovo.com

CHAT_SELECTORS = {
    "amazon.com": {
        "open_chat": None,  # чат открывается вручную
        "messages": "[class*='MessageBubble'], [class*='message-bubble'], .chat-message, [data-testid*='message']",
        "agent_msg": "[class*='agent'], [class*='Agent'], [class*='representative']",
        "input": "textarea[placeholder*='Type'], #chat-input, [class*='chat-input'] textarea",
        "send": "button[id*='send'], [class*='send-button'], [aria-label='Send message']",
    },
    "lenovo.com": {
        # Кнопки открытия чата (видно на скрине 4)
        "open_chat_existing": "button:has-text('Existing Orders')",
        "open_chat_new": "button:has-text('New Order')",
        # Genesys Web Widget — точные селекторы из скрина 5
        "widget_container": "[class*='cx-widget'], [id*='cx-container'], .cx-webchat",
        "messages": ".cx-message, [class*='cx-message'], .cx-bubble, .message",
        # Реальный Lenovo/Powerfront transcript использует .message.operator / .message.visitor
        "agent_msg": ".cx-message:not(.cx-visitor), [class*='cx-agent-message'], .cx-bubble-agent, .message.operator, .message.plain.operator, .lastOperatorMessage",
        # Поле ввода — из скрина: placeholder="Type your message here"
        "input": "textarea[placeholder='Type your message here'], input[placeholder='Type your message here'], .cx-input textarea, [class*='cx-input']",
        # Кнопка отправки — в Genesys обычно иконка/кнопка рядом с полем
        "send": "button.cx-send, [class*='cx-send'], [aria-label*='Send'], button[type='submit']",
    },
    "zara.com": {
        "open_chat": None,
        "messages": "[class*='message'], [class*='chat-bubble'], [class*='conversation']",
        "agent_msg": "[class*='agent'], [class*='operator'], [class*='support']",
        "input": "[class*='chat-input'] input, [placeholder*='message'], [placeholder*='Message']",
        "send": "[class*='send'], button[type='submit']",
    },
    "default": {
        "open_chat": None,
        "messages": "[class*='message'], [class*='chat'], [class*='bubble']",
        "agent_msg": "[class*='agent'], [class*='support'], [class*='operator']",
        "input": "textarea[placeholder*='Type'], textarea[placeholder*='message'], textarea, input[type='text']",
        "send": "button[type='submit'], button[class*='send'], [aria-label*='Send']",
    },
}


# ─── DOLPHIN ANTY API ────────────────────────────────────────────────────────

def dolphin_headers():
    headers = {}
    # Пробуем несколько распространённых схем авторизации Dolphin
    token = DOLPHIN_SESSION_TOKEN.strip()
    cloud_key = DOLPHIN_CLOUD_API_KEY.strip()

    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Session-Token"] = token

    if cloud_key:
        headers["X-API-Key"] = cloud_key
        headers["Authorization"] = headers.get("Authorization", f"Bearer {cloud_key}")

    return headers

def list_profiles():
    """Получить список профилей Dolphin Anty"""
    try:
        r = requests.get(f"{DOLPHIN_API}/browser_profiles",
                        params={"limit": 50}, headers=dolphin_headers(), timeout=5)
        data = r.json()
        if data.get("error") == "invalid session token":
            print("❌ Dolphin Local API требует session token. Укажи DOLPHIN_SESSION_TOKEN.")
            return []
        return data.get("data", [])
    except Exception as e:
        print(f"❌ Dolphin Anty не запущен или порт неверный: {e}")
        return []

def find_profile_by_name(name):
    """Найти профиль по названию (частичное совпадение, без учёта регистра)"""
    profiles = list_profiles()
    name_lower = name.lower().strip()
    # Точное совпадение
    for p in profiles:
        if p.get("name", "").lower() == name_lower:
            return p
    # Частичное совпадение
    for p in profiles:
        if name_lower in p.get("name", "").lower():
            return p
    return None

def start_profile(profile_id):
    """Запустить профиль и получить CDP порт"""
    try:
        r = requests.get(f"{DOLPHIN_API}/browser_profiles/{profile_id}/start",
                        params={"automation": 1}, headers=dolphin_headers(), timeout=15)
        data = r.json()
        if data.get("error") == "invalid session token":
            print("❌ Неверный/отсутствующий session token для Dolphin Local API.")
            return None
        port = data.get("automation", {}).get("port")
        return port
    except Exception as e:
        print(f"❌ Ошибка запуска профиля: {e}")
        return None

def start_profile_public_by_id(profile_id):
    """Публичный запуск профиля через Local API (без session token)."""
    try:
        r = requests.get(f"{DOLPHIN_API}/browser_profiles/{profile_id}/start",
                         params={"automation": 1}, timeout=20)
        data = r.json()
        port = data.get("automation", {}).get("port")
        if port:
            return {"profile_id": str(profile_id), "port": port, "raw": data}

        # Частый кейс: профиль уже запущен вручную.
        # По умолчанию НЕ перезапускаем профиль, чтобы окно не мигало/не закрывалось.
        err = str(data.get("error", "")).lower()
        if "already running" in err:
            if not ALLOW_PROFILE_RESTART:
                return {"profile_id": str(profile_id), "port": None, "raw": data, "already_running": True}
            try:
                requests.get(f"{DOLPHIN_API}/browser_profiles/{profile_id}/stop", timeout=10)
            except Exception:
                pass
            for _ in range(6):
                time.sleep(1)
                try:
                    rr = requests.get(
                        f"{DOLPHIN_API}/browser_profiles/{profile_id}/start",
                        params={"automation": 1},
                        timeout=20,
                    )
                    d2 = rr.json()
                    p2 = d2.get("automation", {}).get("port")
                    if p2:
                        return {"profile_id": str(profile_id), "port": p2, "raw": d2}
                except Exception:
                    continue
        return None
    except Exception:
        return None

def extract_profile_id_from_logs(profile_name):
    """
    Пытается найти browserProfileId по имени профиля в локальных логах Dolphin.
    Полезно, когда список профилей закрыт session-token'ом.
    """
    try:
        logs_dir = Path.home() / "Library" / "Application Support" / "dolphin_anty" / "logs"
        if not logs_dir.exists():
            return None
        escaped = re.escape(profile_name)
        # В логах встречаются разные форматы:
        # ... profile_name:'Luna_CA' ... browser_profile_id:456016554 ...
        # ... profile_name:'Katrin_NJ' ... browserProfileId:597251528 ...
        patterns = [
            re.compile(rf"profile_name:'{escaped}'.*?browser_profile_id:(\d+)"),
            re.compile(rf"profile_name:'{escaped}'.*?browserProfileId:(\d+)"),
            re.compile(rf"name:'{escaped}'.*?browserProfileId:(\d+)"),
            re.compile(rf"name:'{escaped}'.*?browser_profile_id:(\d+)"),
        ]
        # Берём больше файлов, т.к. нужный профиль мог запускаться не сегодня.
        candidates = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]
        for log_path in candidates:
            text = log_path.read_text(errors="ignore")
            for pattern in patterns:
                matches = pattern.findall(text)
                if matches:
                    return matches[-1]
    except Exception:
        return None
    return None

def start_profile_public_by_name(profile_name):
    """
    Запуск профиля по имени без session token.
    1) Пробуем POST /browser_profiles/start?automation=1 с name
    2) Если порт не пришёл, ищем real profile_id в логах и стартуем по ID
    """
    try:
        r = requests.post(
            f"{DOLPHIN_API}/browser_profiles/start",
            params={"automation": 1},
            json={"name": profile_name},
            timeout=20,
        )
        data = r.json()
        port = data.get("automation", {}).get("port")
        profile_id = str(data.get("profileId", ""))
        if port:
            return {"profile_id": profile_id, "port": port, "raw": data}

        # В некоторых случаях API возвращает temporary profileId без automation порта.
        # Тогда подбираем реальный ID из логов и запускаем по ID.
        real_id = extract_profile_id_from_logs(profile_name)
        if real_id:
            started = start_profile_public_by_id(real_id)
            if started:
                return started
    except Exception:
        pass
    return None

def get_running_profiles_public():
    """Публично получить запущенные профили (без session token)."""
    try:
        r = requests.get(f"{DOLPHIN_API}/browser_profiles/running", timeout=8)
        data = r.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}

def restart_running_profile_for_automation():
    """
    Fallback: берём уже запущенный профиль и перезапускаем его в automation=1.
    Нужен, когда профиль стартован вручную и недоступен листинг по токену.
    """
    running = get_running_profiles_public()
    if not running:
        return None

    # Выбираем профиль с самым "свежим" PID как наиболее вероятно активный.
    # running format: { "<profile_id>": {"runid": "...", "pid": 1234}, ... }
    def pid_of(item):
        try:
            return int((item[1] or {}).get("pid") or 0)
        except Exception:
            return 0

    profile_id, _ = sorted(running.items(), key=pid_of, reverse=True)[0]
    try:
        requests.get(f"{DOLPHIN_API}/browser_profiles/{profile_id}/stop", timeout=10)
    except Exception:
        pass

    # Dolphin может не сразу освободить профиль после stop.
    # Делаем несколько попыток старта automation.
    for _ in range(6):
        try:
            time.sleep(1)
        except Exception:
            pass
        started = start_profile_public_by_id(profile_id)
        if started:
            return started
    return None

def stop_profile(profile_id):
    """Остановить профиль"""
    try:
        requests.get(f"{DOLPHIN_API}/browser_profiles/{profile_id}/stop",
                     headers=dolphin_headers(), timeout=5)
    except:
        pass


# ─── CLAUDE AI ───────────────────────────────────────────────────────────────

class CopilotSession:
    def __init__(
        self,
        store,
        case_type,
        order_num="",
        amount="",
        details="",
        customer_name="",
        customer_email="",
        customer_phone="",
    ):
        self.store = store
        self.case_type = case_type
        self.order_num = order_num
        self.amount = amount
        self.details = details
        self.customer_name = customer_name
        self.customer_email = customer_email
        self.customer_phone = customer_phone
        self.history = []
        self.transcript = []
        self.message_count = 0
        self.last_agent_msg = ""
        self.last_sent_msg = ""
        self.last_llm_error = ""

    def case_name(self):
        t = (self.case_type or "").upper()
        if t == "INR":
            return "Item Not Received"
        if t in {"RNR", "REFUND"}:
            return "Refund Not Received After Return"
        if t in {"DOA", "DAMAGED", "DEFECTIVE", "BROKEN"}:
            return "Defective Item Returned, Refund Overdue"
        return "Refund Not Received After Return"

    def case_issue_summary(self):
        t = (self.case_type or "").upper()
        if t == "INR":
            return "the order was not received"
        if t in {"DOA", "DAMAGED", "DEFECTIVE", "BROKEN"}:
            return (
                "the laptop was delivered with a broken screen, Lenovo arranged a replacement, "
                "the replacement was not received and was returned back to Lenovo, and the returned merchandise "
                "has already been delivered back but the refund is still outstanding"
            )
        return "the returned merchandise was delivered back to the merchant but the refund is still outstanding"

    def generate_first_message(self):
        plan = self.plan_next_action(
            agent_text="",
            observation={"chat_ready": True, "first_turn": True},
            first_turn=True,
        )
        reply = (plan.get("message") or "").strip()
        if not reply:
            prompt = f"""Start a live support chat with {self.store}.
Case: {self.case_name()}
Order: {self.order_num or "N/A"}
Amount: {f"${self.amount}" if self.amount else "N/A"}
Details: {self.details or "none"}
Write the first message to the support agent.
Make it polished, grammatical, and natural."""

            self.history = [{"role": "user", "content": prompt}]
            reply = self._call_llm()
        self.history.append({"role": "assistant", "content": reply})
        self.transcript.append({"role": "customer_rep", "content": reply})
        self.message_count = 1
        self.last_sent_msg = reply
        return reply

    def generate_reply(self, agent_text):
        self.record_agent_message(agent_text)
        plan = self.plan_next_action(
            agent_text=agent_text,
            observation={"chat_ready": True, "message_count": self.message_count},
            first_turn=False,
        )
        reply = (plan.get("message") or "").strip()
        if not reply:
            n = self.message_count
            escalation = (
                "Step 2: be firm; ask for supervisor/escalations team and a case ID with timeline." if n == 1 else
                "Step 3: if denial/stalling, request written policy basis; mention FCBA/FTC and dispute rights." if n == 2 else
                "Final step: set a final deadline and request immediate resolution confirmation in writing."
            )
            prompt = f"""Agent replied: "{agent_text}"
{escalation}
Write the next message that maximizes chance of a positive resolution now.
Preferred outcomes priority: full refund > free replacement > supervisor escalation with case ID and deadline.
Before answering, silently proofread the message for grammar and clarity."""
            self.history.append({"role": "user", "content": prompt})
            reply = self._call_llm()
        self.history.append({"role": "assistant", "content": reply})
        self.transcript.append({"role": "customer_rep", "content": reply})
        self.message_count += 1
        self.last_sent_msg = reply
        return reply

    def build_case_snapshot(self):
        return {
            "store": self.store,
            "case_type": self.case_name(),
            "order_num": self.order_num or "",
            "amount": f"{self.amount}" if self.amount else "",
            "details": self.details or "",
            "customer_name": self.customer_name or "",
            "customer_email": self.customer_email or "",
            "customer_phone": self.customer_phone or "",
            "message_count": self.message_count,
            "last_agent_message": self.last_agent_msg or "",
            "last_customer_message": self.last_sent_msg or "",
            "agent_intent": self.infer_agent_intent(self.last_agent_msg),
            "current_objective": self.current_objective(),
        }

    def record_agent_message(self, agent_text):
        agent_text = (agent_text or "").strip()
        if not agent_text:
            return
        self.last_agent_msg = agent_text
        self.transcript.append({"role": "agent", "content": agent_text})

    def should_send_message(self, message):
        normalized = self._normalize_message(message)
        if not normalized:
            return False
        last = self._normalize_message(self.last_sent_msg)
        if normalized == last:
            return False
        if last and difflib.SequenceMatcher(a=normalized, b=last).ratio() >= 0.88:
            return False
        return True

    def mark_message_sent(self, message):
        self.last_sent_msg = (message or "").strip()

    def infer_agent_intent(self, text):
        t = (text or "").lower()
        if any(x in t for x in ["still connected", "checking in to confirm whether we are still connected"]):
            return "keepalive"
        if any(x in t for x in ["contact ups", "reach out to ups", "ups drop-off center", "ups for the order confirmation"]):
            return "ups_redirect"
        if "chat transcript" in t:
            return "transcript_offer"
        if any(x in t for x in ["will be escalated", "escalated to the returns team"]):
            return "escalation_confirmed"
        if any(x in t for x in ["case id", "c004094813"]) and any(x in t for x in ["here is", "shared", "raised"]):
            return "case_id_provided"
        if "empty box" in t or "box was empty" in t:
            return "empty_box_claim"
        if any(x in t for x in ["warehouse has not received", "not received the returned item", "not received the return"]):
            return "warehouse_missing_claim"
        if any(x in t for x in ["5-7 business days", "processed within", "resolution timeline"]):
            return "timeline_statement"
        return "general"

    def current_objective(self):
        intent = self.infer_agent_intent(self.last_agent_msg)
        if intent == "keepalive":
            return "confirm connection and force a concrete next step"
        if intent == "ups_redirect":
            return "push Lenovo to coordinate internally with UPS and keep the case escalated"
        if intent == "empty_box_claim":
            return "demand written basis and returns-team escalation for the empty-box claim"
        if intent == "warehouse_missing_claim":
            return "highlight the contradiction and force Lenovo to clarify lost return versus empty-box claim"
        if intent == "case_id_provided":
            return "turn the case ID into a confirmed escalation with a written timeline"
        if intent == "escalation_confirmed":
            return "obtain written resolution timeline and exact dispute classification"
        if intent == "transcript_offer":
            return "accept the transcript only if escalation and timeline are also confirmed"
        return "push toward refund, escalation, written basis, and timeline"

    def plan_next_action(self, agent_text="", observation=None, first_turn=False):
        observation = observation or {}
        transcript_tail = self.transcript[-8:]
        user_prompt = {
            "case": self.build_case_snapshot(),
            "first_turn": bool(first_turn),
            "latest_agent_message": agent_text or "",
            "observation": observation,
            "transcript_tail": transcript_tail,
            "priority": [
                "full refund",
                "free replacement or reshipment",
                "supervisor or escalations ticket with deadline",
            ],
        }
        try:
            raw = self._call_llm(
                system_prompt=AGENT_DECISION_PROMPT,
                history=[{"role": "user", "content": json.dumps(user_prompt, ensure_ascii=True)}],
                temperature=0.1,
                sanitize=False,
            )
        except Exception as e:
            self.last_llm_error = str(e)
            if observation.get("chat_ready") or first_turn:
                fallback_message = self._fallback_message(agent_text=agent_text, first_turn=first_turn)
                return {
                    "action": "send_message" if fallback_message else "wait",
                    "message": fallback_message,
                    "goal": "fallback_send_message" if fallback_message else "wait_for_valid_state",
                    "reason": f"llm_unavailable: {self.last_llm_error}",
                    "confidence": 0.2,
                }
            return {
                "action": "wait",
                "message": "",
                "goal": "wait_for_valid_state",
                "reason": f"llm_unavailable: {self.last_llm_error}",
                "confidence": 0.0,
            }
        plan = self._extract_json_object(raw)
        if not isinstance(plan, dict):
            return {"action": "wait", "message": "", "goal": "", "reason": "json_parse_failed", "confidence": 0.0}
        action = (plan.get("action") or "wait").strip().lower()
        if action not in {"send_message", "wait", "finish"}:
            action = "wait"
        message = self._sanitize_reply(plan.get("message") or "") if action == "send_message" else ""
        if action == "send_message" and self._looks_like_role_inversion(message):
            message = self._fallback_message(agent_text=agent_text, first_turn=first_turn)
        if action == "send_message" and not self._message_addresses_intent(message, agent_text):
            message = self._fallback_message(agent_text=agent_text, first_turn=first_turn)
        return {
            "action": action,
            "message": message,
            "goal": (plan.get("goal") or "").strip(),
            "reason": (plan.get("reason") or "").strip(),
            "confidence": float(plan.get("confidence") or 0.0),
        }

    def _fallback_message(self, agent_text="", first_turn=False):
        issue = self.case_issue_summary()
        order = self.order_num or "my order"
        if first_turn:
            if (self.case_type or "").upper() in {"DOA", "DAMAGED", "DEFECTIVE", "BROKEN"}:
                return (
                    f"I need help with order {order}. "
                    "The laptop was delivered with a broken screen, Lenovo arranged a replacement, the replacement was not received and returned back to Lenovo, and the returned merchandise has already been delivered back. "
                    "Please confirm the full refund and the exact processing timeline today."
                )
            return (
                f"Hello, I need help with order {order}. "
                f"The issue is that {issue}. "
                "Please review the case and confirm the fastest resolution available today."
            )

        t = (agent_text or "").lower()
        if any(x in t for x in ["still connected", "checking in to confirm whether we are still connected", "are we still connected"]):
            return (
                "Yes, we are still connected. "
                "Please confirm whether case ID C004094813 has already been escalated to the returns team and provide the written resolution timeline today."
            )
        if "will be escalated" in t and any(x in t for x in ["case id", "c004094813", "returns team"]):
            return (
                "Thank you for confirming the escalation. "
                "Please confirm the written resolution timeline for case ID C004094813 today and clarify whether Lenovo is treating this as an empty-box claim or a lost return."
            )
        if any(x in t for x in ["contact ups", "reach out to ups", "contact the ups drop-off center", "ups for the order confirmation"]):
            return (
                "The return used Lenovo's UPS label, so Lenovo should coordinate with UPS internally if Lenovo is disputing the contents of the return. "
                "Please keep case ID C004094813 escalated with the returns team and confirm the written basis for withholding the refund plus the exact resolution timeline today."
            )
        if any(x in t for x in ["warehouse has not received", "not received the returned item", "not received the return", "returned item not received"]):
            return (
                "Your updates are inconsistent because Lenovo previously stated that the return was received back, and now you are stating that the warehouse did not receive it. "
                "Please escalate this discrepancy to the returns team today, provide the case ID for that escalation, and confirm in writing whether Lenovo is treating this as a lost return or an empty-box claim."
            )
        if any(x in t for x in ["email", "e-mail"]):
            return (
                f"The email on the order is {normalize_customer_email(self.customer_email)}. "
                "Please confirm the next step and timeline after you review it."
            )
        if any(x in t for x in ["ups", "receipt", "drop-off", "drop off", "empty box"]):
            return (
                f"The return for order {order} was sent using Lenovo's UPS label and Lenovo's own update states that the return was received back. "
                "If Lenovo is asserting an empty-box exception, please escalate this to the returns team today, provide the case ID, and confirm the written basis for withholding the refund."
            )
        if "phone" in t:
            return (
                f"The phone number on the order is {normalize_customer_phone(self.customer_phone)}. "
                "Please confirm the next step and timeline after you review it."
            )
        if "order" in t and any(x in t for x in ["number", "#", "num"]):
            return (
                f"The order number is {normalize_order_num(self.order_num)}. "
                "Please review it and confirm what resolution you can provide today."
            )
        if "name" in t:
            return (
                f"The customer name is {normalize_customer_name(self.customer_name)}. "
                "Please confirm the next step once you verify the account."
            )
        if any(x in t for x in ["case id", "ticket", "reference"]):
            return "Thank you. Please provide the case ID and confirm the escalation timeline in writing today."
        if any(x in t for x in ["cannot", "unable", "policy", "denied", "decline"]):
            return (
                "Please escalate this to a supervisor or escalations team and provide the policy basis in writing today. "
                "Also confirm the case ID and deadline for resolution."
            )
        if (self.case_type or "").upper() in {"DOA", "DAMAGED", "DEFECTIVE", "BROKEN"}:
            return (
                f"Lenovo has already received the returned merchandise for order {order}, so the refund should not remain outstanding. "
                "Please confirm whether you will complete the full refund now or escalate this to the refunds team today with a case ID and timeline."
            )
        return (
            f"I need a concrete update on order {order}. "
            "Please confirm whether you can resolve this with a refund, replacement, or escalation today."
        )

    def _extract_json_object(self, raw):
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None

    def _call_llm(self, system_prompt=None, history=None, temperature=None, sanitize=True):
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        system_prompt = system_prompt or SYSTEM_PROMPT
        history = history if history is not None else self.history
        payload = {
            "model": OPENAI_MODEL,
            "messages": [{"role": "system", "content": system_prompt}] + history,
            "max_tokens": 700,
            "temperature": 0.2 if temperature is None else temperature,
        }

        r = requests.post(OPENAI_API_URL, headers=headers, json=payload, timeout=60)
        data = r.json()
        if r.status_code >= 400:
            err = data.get("error", {}).get("message") or data
            raise RuntimeError(f"OpenAI API error: {err}")

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenAI API returned empty choices: {data}")
        raw = (choices[0].get("message", {}).get("content") or "").strip()
        return self._sanitize_reply(raw) if sanitize else raw

    def _sanitize_reply(self, text):
        """Оставляем только сообщение для отправки в чат, без объяснений модели."""
        if not text:
            return ""
        t = text.strip()

        # Если модель вернула блок "Next Message", вытаскиваем цитату.
        markers = ["**Next Message:**", "Next Message:"]
        for marker in markers:
            idx = t.find(marker)
            if idx != -1:
                chunk = t[idx + len(marker):].strip()
                m = re.search(r'"([^"]{8,1200})"', chunk, flags=re.S)
                if m:
                    return " ".join(m.group(1).split())

        # Обрезаем объяснительные блоки.
        for cut in ["**Why this works:**", "Why this works:", "Reasoning:", "Explanation:"]:
            idx = t.find(cut)
            if idx != -1:
                t = t[:idx].strip()

        # Удаляем markdown шум.
        t = re.sub(r"^\*+|\*+$", "", t).strip()
        t = re.sub(r"\n{2,}", "\n", t).strip()
        return t

    def _normalize_message(self, text):
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    def _message_addresses_intent(self, message, agent_text):
        msg = self._normalize_message(message)
        intent = self.infer_agent_intent(agent_text)
        if not msg:
            return False
        if intent == "keepalive":
            return "connected" in msg or msg.startswith("yes")
        if intent == "ups_redirect":
            return "ups" in msg and ("internally" in msg or "returns team" in msg or "escalat" in msg)
        if intent == "transcript_offer":
            return "transcript" in msg or "timeline" in msg or "escalat" in msg
        if intent == "case_id_provided":
            return "c004094813" in msg or "case id" in msg
        if intent == "escalation_confirmed":
            return "timeline" in msg or "written" in msg or "empty-box" in msg or "lost return" in msg
        if intent == "empty_box_claim":
            return "empty box" in msg or "written basis" in msg or "returns team" in msg
        if intent == "warehouse_missing_claim":
            return "inconsistent" in msg or "received back" in msg or "lost return" in msg
        return True

    def _looks_like_role_inversion(self, text):
        t = (text or "").strip().lower()
        if not t:
            return False
        bad_starts = (
            "i understand your concern",
            "i understand the urgency",
            "i will check",
            "i'll check",
            "let me check",
            "please hold on",
            "hold on for a moment",
            "i can understand your concern",
        )
        if t.startswith(bad_starts):
            return True
        bad_fragments = [
            "our returns department",
            "our team",
            "our records",
            "our internal team",
            "i will verify",
            "i will review",
            "i will provide you with an update",
            "while i gather this information",
            "please allow me",
        ]
        return any(fragment in t for fragment in bad_fragments)


# ─── CHAT READER/WRITER ──────────────────────────────────────────────────────

def get_store_name(url):
    for key in CHAT_SELECTORS:
        if key in url and key != "default":
            return key
    return "default"

def preferred_store_domain(store_name):
    s = (store_name or "").lower()
    if "lenovo" in s:
        return "lenovo.com"
    if "amazon" in s:
        return "amazon.com"
    if "zara" in s:
        return "zara.com"
    if "walmart" in s:
        return "walmart.com"
    if "ebay" in s:
        return "ebay.com"
    return None

async def pick_best_page(context, preferred_domain=None):
    """
    Выбирает наиболее релевантную вкладку для чата (а не сервисные вкладки типа Outlook).
    """
    best_page = None
    best_score = -10**9
    for p in context.pages:
        url = (p.url or "").lower()
        score = 0
        if not url or url == "about:blank":
            score -= 100
        if preferred_domain and preferred_domain in url:
            score += 100
        if any(k in url for k in ["lenovo.com", "amazon.", "zara.com", "walmart.", "ebay."]):
            score += 40
        if any(k in url for k in ["chat", "support", "contact", "help"]):
            score += 20
        if any(k in url for k in ["outlook.live.com", "mail.", "gmail.com", "telegram.", "web.whatsapp"]):
            score -= 60
        if score > best_score:
            best_score = score
            best_page = p
    return best_page

async def read_last_agent_message(page, store):
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        def looks_like_system_noise(text):
            raw = (text or "").strip()
            t = raw.lower()
            normalized = re.sub(r"\s+", " ", t)
            if not t or len(t) < 5:
                return True
            blocked_exact = {
                "chat with us",
                "existing orders",
                "general question",
                "operator",
                "consumer",
                "start a new chat",
                "type your message here",
                "ai chatbot by powerfronttm",
                "back to main menu",
                "chat to human",
                "request video chat",
                "schedule appointment",
                "print transcript",
                "leave a message",
                "attach a file",
                "end chat",
                "join the call",
                "click to call",
                "cookies opt-out",
            }
            if normalized in blocked_exact:
                return True
            blocked_prefixes = (
                "ai chatbot by powerfront",
                "welcome to lenovo support",
                "welcome to lenovo",
                "one moment please while i transfer you",
            )
            if normalized.startswith(blocked_prefixes):
                return True
            blocked_fragments = [
                "one moment",
                "transfer",
                "connecting",
                "please wait",
                "queue",
                "ai chatbot by powerfront",
                "powerfront",
                "lenovo online sales support",
                "back to main menu",
                "chat to human",
                "request video chat",
                "schedule appointment",
                "print transcript",
                "leave a message",
                "attach a file",
                "click to call",
                "cookies opt-out",
                "welcome to lenovo",
                "how can we help you today?",
                "chat via whatsapp",
                "new order / product",
                "technical support",
                "more resources",
                "check order status",
                "check repair status",
                "price match policy",
                "faqs",
                "invalid information",
                "please provide a correct email format",
                "what's your name",
                "please enter your email address",
                "please enter your phone number",
                "order number",
                "this chat may be monitored",
                "your chat transcript",
            ]
            if any(p in normalized for p in blocked_fragments):
                return True
            # Mixed control/menu panels often come through as one blob.
            if sum(
                phrase in normalized
                for phrase in (
                    "chat to human",
                    "request video chat",
                    "schedule appointment",
                    "print transcript",
                    "leave a message",
                    "attach a file",
                )
            ) >= 2:
                return True
            # System prompts are not actionable operator replies.
            if re.search(r"\(\d+\s+of\s+\d+\)", normalized):
                return True
            return False

        frames = [page.main_frame] + list(page.frames)

        # Сначала пробуем точный селектор агентских сообщений
        agent_sel = sel.get("agent_msg", sel.get("messages", ""))
        agent_msgs = []
        for frame in frames:
            elements = await frame.query_selector_all(agent_sel)
            for el in elements:
                text = await el.inner_text()
                text = text.strip()
                if looks_like_system_noise(text):
                    continue
                agent_msgs.append(text)

        if agent_msgs:
            return agent_msgs[-1]

        # Fallback: читаем все сообщения и фильтруем по классу
        for frame in frames:
            all_elements = await frame.query_selector_all(sel["messages"])
            for el in reversed(all_elements):
                text = (await el.inner_text()).strip()
                if looks_like_system_noise(text):
                    continue
                cls = (await el.get_attribute("class") or "").lower()
                is_ours = any(w in cls for w in ["visitor", "customer", "user", "outgoing", "sent"])
                if not is_ours:
                    return text
        # Универсальный fallback для iframe-чатов:
        # читаем "последнюю осмысленную строку" из видимого текста контейнера чата.
        for frame in frames:
            try:
                fallback_msg = await frame.evaluate(
                    """
                    () => {
                      const roots = Array.from(document.querySelectorAll('body, [class*="chat"], [id*="chat"], [class*="cx-"], [id*="cx-"]'));
                      let best = "";
                      for (const root of roots) {
                        const text = (root.innerText || "").split("\\n").map(s => s.trim()).filter(Boolean);
                        if (!text.length) continue;
                        const last = text[text.length - 1];
                        if (last.length >= 6 && last.length <= 800) best = last;
                      }
                      return best || null;
                    }
                    """
                )
                if fallback_msg:
                    fallback_msg = fallback_msg.strip()
                    if not looks_like_system_noise(fallback_msg):
                        return fallback_msg
            except Exception:
                continue
        return None
    except:
        return None

async def type_message(page, store, text):
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        if store == "lenovo.com":
            try:
                widget_text = (await get_lenovo_widget_text(page)).lower()
                widget_state = classify_lenovo_widget_state(widget_text)
                if widget_state in {"name", "email", "phone", "order", "existing_pick", "general_pick", "operator_pick", "consumer_pick"}:
                    return False
            except Exception:
                pass
            frames = [page.main_frame] + list(page.frames)
            for frame in frames:
                try:
                    ok = await frame.evaluate(
                        """
                        (msg) => {
                          const candidates = [
                            document.querySelector("#chatInput"),
                            document.querySelector("textarea[aria-label='Type your message here']"),
                            ...Array.from(document.querySelectorAll("#insideWorkflowFieldCell input[aria-label], #insideWorkflowFieldCell textarea[aria-label]"))
                              .filter((el) => {
                                const aria = (el.getAttribute("aria-label") || "").toLowerCase();
                                return aria.includes("how can we help you today") && !/\\(\\d+ of \\d+\\)/.test(aria);
                              }),
                          ].filter(Boolean);
                          const ta = candidates[0];
                          if (!ta) return false;
                          const proto = ta.tagName === "TEXTAREA"
                            ? window.HTMLTextAreaElement.prototype
                            : window.HTMLInputElement.prototype;
                          const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
                          if (setter) setter.call(ta, msg);
                          else ta.value = msg;
                          ta.focus();
                          ta.dispatchEvent(new Event("input", { bubbles: true }));
                          ta.dispatchEvent(new Event("change", { bubbles: true }));
                          return true;
                        }
                        """,
                        text,
                    )
                    if ok:
                        return True
                except Exception:
                    continue
        try:
            await page.bring_to_front()
        except Exception:
            pass
        # Перед вводом принудительно раскрываем чатовый виджет.
        await click_floating_chat_launcher(page)
        await click_first_visible(page, [
            "button:has-text('Open chat')",
            "button:has-text('Chat')",
            "button:has-text('Live Chat')",
            "button:has-text('Continue')",
        ])
        await page.wait_for_timeout(250)

        frames = [page.main_frame] + list(page.frames)
        fallback_inputs = [
            sel["input"],
            "textarea[placeholder*='message' i]",
            "textarea[placeholder*='type' i]",
            "textarea",
            "input[placeholder*='message' i]",
            "input[type='text']",
        ]
        async def is_chat_like_input(el):
            try:
                ph = ((await el.get_attribute("placeholder")) or "").lower()
                nm = ((await el.get_attribute("name")) or "").lower()
                el_id = ((await el.get_attribute("id")) or "").lower()
                aria = ((await el.get_attribute("aria-label")) or "").lower()
                cls = ((await el.get_attribute("class")) or "").lower()
                sig = " ".join([ph, nm, el_id, aria])
                # Жестко исключаем поисковые поля.
                if any(k in sig for k in ["search", "find", "lookup", "query", "country", "region", "state", "zip", "postal"]):
                    return False
                # Предпочитаем явные чатовые поля.
                if any(k in sig for k in ["message", "type your message", "chat", "reply", "ask"]):
                    return True
                # Для Lenovo/Genesys допускаем только явно чатовые классы.
                if any(k in cls for k in ["cx-input", "chat", "widget", "messag"]):
                    return True
                # Пустую сигнатуру больше не принимаем, чтобы не писать в случайные поля страницы.
                return False
            except Exception:
                return False

        for frame in frames:
            for input_sel in fallback_inputs:
                input_el = await frame.query_selector(f"{input_sel}:visible")
                if input_el:
                    if not await is_chat_like_input(input_el):
                        continue
                    await input_el.click(force=True)
                    await input_el.fill("")
                    await input_el.type(text, delay=30)  # человекоподобный ввод
                    return True
        # Fallback для contenteditable (часто в чат-виджетах)
        for frame in frames:
            editable = await frame.query_selector("[contenteditable='true']:visible, [role='textbox']:visible")
            if editable:
                await editable.click(force=True)
                await editable.fill("")
                await editable.type(text, delay=20)
                return True
        # Жесткий JS fallback: ищем видимый editable/input и ставим текст напрямую.
        for frame in frames:
            ok = await frame.evaluate(
                """
                (msg) => {
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 2 && r.height > 2 && st.visibility !== 'hidden' && st.display !== 'none';
                  };
                  const isChatLike = (el) => {
                    const sig = [
                      (el.getAttribute("placeholder") || ""),
                      (el.getAttribute("name") || ""),
                      (el.getAttribute("id") || ""),
                      (el.getAttribute("aria-label") || ""),
                      (el.getAttribute("class") || "")
                    ].join(" ").toLowerCase();
                    if (/(search|find|lookup|query|country|region|state|zip|postal)/.test(sig)) return false;
                    if (/(message|type your message|chat|reply|cx-input|widget)/.test(sig)) return true;
                    return false;
                  };
                  const cands = Array.from(document.querySelectorAll(
                    "textarea, input[type='text'], input:not([type]), [contenteditable='true'], [role='textbox']"
                  )).filter((el) => visible(el) && isChatLike(el));
                  const el = cands[0];
                  if (!el) return false;
                  el.focus();
                  if (el.isContentEditable) {
                    el.textContent = "";
                    document.execCommand("insertText", false, msg);
                    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: msg, inputType: "insertText" }));
                  } else {
                    el.value = msg;
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                  }
                  return true;
                }
                """,
                text,
            )
            if ok:
                return True
    except Exception as e:
        print(f"  ⚠️  Не удалось вставить текст: {e}")
    return False

async def send_message(page, store):
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        frames = [page.main_frame] + list(page.frames)
        if store == "lenovo.com":
            for frame in frames:
                try:
                    sent = await frame.evaluate(
                        """
                        () => {
                          const btn = document.querySelector("#chatSendButton");
                          if (!btn) return false;
                          const sig = ((btn.className || "") + " " + (btn.getAttribute("aria-label") || "")).toLowerCase();
                          if (sig.includes("disabled")) return false;
                          const r = btn.getBoundingClientRect();
                          btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                          btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                          btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                          if (typeof btn.click === "function") btn.click();
                          return true;
                        }
                        """
                    )
                    if sent:
                        return True
                except Exception:
                    continue
            for frame in frames:
                try:
                    sent = await frame.evaluate(
                        """
                        () => {
                          const input = Array.from(document.querySelectorAll("#insideWorkflowFieldCell input[aria-label], #insideWorkflowFieldCell textarea[aria-label]"))
                            .find((el) => {
                              const aria = (el.getAttribute("aria-label") || "").toLowerCase();
                              return aria.includes("how can we help you today") && !/\\(\\d+ of \\d+\\)/.test(aria);
                            });
                          if (!input) return false;
                          input.focus();
                          ["keydown", "keypress", "keyup"].forEach((type) => {
                            input.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
                          });
                          return true;
                        }
                        """
                    )
                    if sent:
                        return True
                except Exception:
                    continue
        for frame in frames:
            btn = await frame.query_selector(f"{sel['send']}:visible")
            if btn:
                await btn.click()
                return True
        def is_safe_chat_selector(base_sel):
            # Никогда не отправляем Enter по глобальным полям выбора региона/поиска.
            lowered = (base_sel or "").lower()
            bad = ["country", "region", "search", "find", "postal", "zip", "state"]
            return not any(b in lowered for b in bad)

        # Fallback: Enter
        for frame in frames:
            input_sel = f"{sel['input']}:visible"
            if not is_safe_chat_selector(input_sel):
                continue
            input_el = await frame.query_selector(input_sel)
            if input_el:
                try:
                    ph = ((await input_el.get_attribute("placeholder")) or "").lower()
                    if any(k in ph for k in ["country", "region", "search", "find"]):
                        continue
                except Exception:
                    pass
                await input_el.press("Enter")
                return True
        # Fallback: Enter в contenteditable
        for frame in frames:
            editable = await frame.query_selector("[contenteditable='true']:visible, [role='textbox']:visible")
            if editable:
                await editable.press("Enter")
                return True
        # Жесткий JS fallback: отправка Enter в активный/последний видимый инпут.
        for frame in frames:
            ok = await frame.evaluate(
                """
                () => {
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 2 && r.height > 2 && st.visibility !== 'hidden' && st.display !== 'none';
                  };
                  const cands = Array.from(document.querySelectorAll(
                    "textarea, input[type='text'], input:not([type]), [contenteditable='true'], [role='textbox']"
                  )).filter(visible);
                  const el = cands[cands.length - 1] || document.activeElement;
                  if (!el) return false;
                  el.focus();
                  const evt = new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true });
                  el.dispatchEvent(evt);
                  const evt2 = new KeyboardEvent("keyup", { key: "Enter", code: "Enter", bubbles: true });
                  el.dispatchEvent(evt2);
                  return true;
                }
                """
            )
            if ok:
                return True
    except:
        pass

async def human_send_delay(text, min_seconds=4.0, max_seconds=16.0):
    """Короткая пауза перед отправкой, чтобы бот не отвечал мгновенно."""
    length = len(re.sub(r"\s+", " ", (text or "").strip()))
    base = 4.0 + min(length / 85.0, 7.0)
    pause = min(max(base + random.uniform(-1.2, 2.8), min_seconds), max_seconds)
    print(f"⏳ Human pause before send: {pause:.1f}s")
    await asyncio.sleep(pause)
    return False

async def click_first_visible(page, selectors):
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        for sel in selectors:
            try:
                el = await frame.query_selector(sel)
                if el:
                    await el.click(timeout=1200, force=True)
                    print(f"  ℹ️  Clicked selector: {sel}")
                    return True
            except Exception:
                continue
    return False

async def click_by_text_deep(page, texts):
    """
    Клик по элементу по тексту даже внутри shadow DOM (во всех фреймах).
    texts: список фраз, достаточно частичного совпадения (case-insensitive).
    """
    frames = [page.main_frame] + list(page.frames)
    lowered = [t.lower() for t in texts if t]
    for frame in frames:
        try:
            clicked = await frame.evaluate(
                """
                (targets) => {
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 8 && r.height > 8 && st.visibility !== "hidden" && st.display !== "none";
                  };
                  const isClickable = (el) => {
                    const tag = (el.tagName || "").toLowerCase();
                    return tag === "button" || tag === "a" || el.getAttribute("role") === "button" || !!el.onclick;
                  };
                  const isHeaderControl = (el) => {
                    const sig = [
                      el.getAttribute("aria-label") || "",
                      el.getAttribute("title") || "",
                      el.className || "",
                      el.id || "",
                    ].join(" ").toLowerCase();
                    return /(close|collapse|minimize|settings|menu|gear|icon-close|icon-collapse)/.test(sig);
                  };
                  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
                  const ownText = (el) => norm((el.innerText || "").slice(0, 200));
                  const textMatches = (el, targets) => {
                    const t = ownText(el);
                    if (!t) return false;
                    return targets.some(x => t.includes(x));
                  };
                  const closestClickable = (el) => {
                    let cur = el;
                    for (let i = 0; i < 8 && cur; i++) {
                      if (isClickable(cur)) return cur;
                      cur = cur.parentElement;
                    }
                    return null;
                  };
                  const parentChain = (el) => {
                    const out = [];
                    let cur = el;
                    for (let i = 0; i < 8 && cur; i++) {
                      out.push(cur);
                      cur = cur.parentElement;
                    }
                    return out;
                  };
                  const collect = (root, out) => {
                    const nodes = root.querySelectorAll("*");
                    for (const n of nodes) {
                      out.push(n);
                      if (n.shadowRoot) collect(n.shadowRoot, out);
                    }
                  };
                  const all = [];
                  collect(document, all);
                  for (const el of all) {
                    if (!visible(el)) continue;
                    if (!textMatches(el, targets)) continue;
                    const candidates = [];
                    const c = closestClickable(el);
                    if (c) candidates.push(c);
                    for (const p of parentChain(el)) candidates.push(p);
                    for (const target of candidates) {
                      if (!target || !visible(target)) continue;
                      if (isHeaderControl(target)) continue;
                      try {
                        target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                        if (typeof target.click === "function") target.click();
                        return true;
                      } catch (_) {}
                    }
                  }
                  return false;
                }
                """,
                lowered,
            )
            if clicked:
                return True
        except Exception:
            continue
    return False

async def click_lenovo_option(page, label):
    """Точечный клик по опции внутри LenovoAdvisor (по тексту кнопки)."""
    target = (label or "").strip()
    if not target:
        return False
    clicked = await click_by_text_deep(page, [target])
    if clicked:
        print(f"  ✅ Lenovo step: {target}")
        await page.wait_for_timeout(450)
        return True
    return False

async def click_lenovo_contact_chat_cta(page):
    """
    На странице Lenovo Order Support CTA `CHAT WITH US` часто рендерится как div-контейнер,
    который лучше открывать через scroll + JS click, а не через общий selector click.
    """
    frames = [page.main_frame] + list(page.frames)
    priority_selectors = [
        "#contactServiceContainer",
        "#or_chat_customer",
        "#contactBusinessSalesContainer",
        "#or_chat_smb",
        "#contactServiceLink",
        "#contactServiceLinkInfo",
    ]
    for frame in frames:
        for sel in priority_selectors:
            try:
                ok = await frame.evaluate(
                    """
                    (selector) => {
                      const el = document.querySelector(selector);
                      if (!el) return false;
                      const visible = (node) => {
                        const r = node.getBoundingClientRect();
                        const st = getComputedStyle(node);
                        return r.width > 8 && r.height > 8 && st.display !== "none" && st.visibility !== "hidden";
                      };
                      if (!visible(el)) return false;
                      el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
                      const r = el.getBoundingClientRect();
                      const x = r.left + r.width / 2;
                      const y = r.top + r.height / 2;
                      const fire = (type) => el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
                      fire("mousemove");
                      fire("mousedown");
                      fire("mouseup");
                      fire("click");
                      if (typeof el.click === "function") el.click();
                      return true;
                    }
                    """,
                    sel,
                )
                if ok:
                    print(f"  ℹ️  Lenovo CTA click via selector: {sel}")
                    await page.wait_for_timeout(700)
                    return True
            except Exception:
                continue
        try:
            ok = await frame.evaluate(
                """
                () => {
                  const visible = (node) => {
                    const r = node.getBoundingClientRect();
                    const st = getComputedStyle(node);
                    return r.width > 8 && r.height > 8 && st.display !== "none" && st.visibility !== "hidden";
                  };
                  const candidates = Array.from(document.querySelectorAll("button, a, div, span"))
                    .filter(el => visible(el) && /chat\\s+with\\s+us/i.test((el.innerText || "") + " " + (el.getAttribute("aria-label") || "")));
                  if (!candidates.length) return false;
                  candidates.sort((a, b) => {
                    const ra = a.getBoundingClientRect();
                    const rb = b.getBoundingClientRect();
                    return (ra.top - rb.top) || (ra.left - rb.left);
                  });
                  const el = candidates[0];
                  el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
                  const r = el.getBoundingClientRect();
                  const x = r.left + r.width / 2;
                  const y = r.top + r.height / 2;
                  const fire = (type) => el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
                  fire("mousemove");
                  fire("mousedown");
                  fire("mouseup");
                  fire("click");
                  if (typeof el.click === "function") el.click();
                  return true;
                }
                """
            )
            if ok:
                print("  ℹ️  Lenovo CTA click via text fallback: CHAT WITH US")
                await page.wait_for_timeout(700)
                return True
        except Exception:
            continue
    return False

async def restart_expired_lenovo_chat(page):
    """
    Если Lenovo workflow протух и показывает только START A NEW CHAT,
    перезапускаем виджет из текущего iframe-состояния.
    """
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        try:
            restarted = await frame.evaluate(
                """
                () => {
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 8 && r.height > 8 && st.display !== "none" && st.visibility !== "hidden";
                  };
                  const txt = (document.body && document.body.innerText ? document.body.innerText : "").toLowerCase();
                  if (!txt.includes("start a new chat")) return false;
                  const cands = Array.from(document.querySelectorAll("#startANewChatButton, .startANewChatButton, .picklistOption, span, div, a"))
                    .filter((el) => visible(el));
                  for (const el of cands) {
                    const text = ((el.innerText || "") + " " + (el.getAttribute("aria-label") || "")).toLowerCase();
                    if (!text.includes("start a new chat")) continue;
                    const r = el.getBoundingClientRect();
                    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                    el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                    if (typeof el.click === "function") el.click();
                    return true;
                  }
                  return false;
                }
                """
            )
            if restarted:
                print("  ✅ Lenovo step: Start a new chat")
                await page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    # Fallback for stale/closed Powerfront state: reset from the outer Lenovo page CTA.
    try:
        has_expired = False
        for frame in frames:
            try:
                txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.toLowerCase() : ''")
                if "start a new chat" in (txt or ""):
                    has_expired = True
                    break
            except Exception:
                continue
        if has_expired:
            try:
                await page.evaluate(
                    """
                    () => {
                      const pane = document.querySelector("#insideChatPane");
                      if (pane) pane.classList.add("closed");
                      const holder = document.querySelector("#inside_holder");
                      if (holder) holder.classList.remove("chatPaneOpen");
                      const iframe = document.querySelector("#insideChatFrame");
                      if (iframe) iframe.style.pointerEvents = "none";
                    }
                    """
                )
            except Exception:
                pass
            reopened = await click_lenovo_contact_chat_cta(page)
            if reopened:
                print("  ✅ Lenovo step: Restart via outer chat CTA")
                await page.wait_for_timeout(900)
                return True
    except Exception:
        pass
    return False

async def click_lenovo_picklist_option(page, labels):
    """
    Клик по опциям Lenovo insideChatFrame, где шаги рендерятся как div.picklistOption.
    """
    wanted = {(x or "").strip().lower() for x in labels if (x or "").strip()}
    if not wanted:
        return False
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        try:
            clicked = await frame.evaluate(
                """
                (wantedArr) => {
                  const wanted = new Set((wantedArr || []).map(x => String(x || "").trim().toLowerCase()).filter(Boolean));
                  const norm = (s) => String(s || "").replace(/\\s+/g, " ").trim().toLowerCase();
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 8 && r.height > 8 && st.visibility !== "hidden" && st.display !== "none";
                  };
                  const roots = Array.from(document.querySelectorAll(
                    "#insideChatPane, #inside_holder, .picklist, .picklistOptions, .workflowBubble, .messageContent"
                  ));
                  if (!roots.length) return false;
                  const inWidget = (el) => roots.some((root) => root.contains(el));
                  const cands = Array.from(document.querySelectorAll(".picklistOption, .picklistOptionLink, .picklistContent, .text, span, div, a"))
                    .filter((el) => visible(el) && inWidget(el));
                  for (const el of cands) {
                    const text = norm((el.innerText || "").slice(0, 120));
                    if (!text || text.length > 80) continue;
                    if (!wanted.has(text)) continue;
                    const r = el.getBoundingClientRect();
                    el.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                    el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                    if (typeof el.click === "function") el.click();
                    return true;
                  }
                  return false;
                }
                """,
                list(wanted),
            )
            if clicked:
                return True
        except Exception:
            continue
    return False

async def click_lenovo_button_exact(page, labels):
    """
    Lenovo-specific: клик только по кнопке/ссылке внутри чат-виджета
    с точным текстом, чтобы не нажимать иконки хедера.
    """
    wanted = {(x or "").strip().lower() for x in labels if (x or "").strip()}
    if not wanted:
        return False
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        try:
            clicked = await frame.evaluate(
                """
                (wantedArr) => {
                  const wanted = new Set((wantedArr || []).map(x => String(x || "").trim().toLowerCase()).filter(Boolean));
                  const norm = (s) => String(s || "").replace(/\\s+/g, " ").trim().toLowerCase();
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 8 && r.height > 8 && st.visibility !== "hidden" && st.display !== "none";
                  };
                  const inWidget = (el) => !!el.closest(
                    "[class*='cx-widget'], [id*='cx-container'], .cx-webchat, [class*='webchat' i], [class*='genesys' i], [class*='chat' i], #insideChatPane, #inside_holder, .picklist, .workflowBubble"
                  );
                  const cands = Array.from(document.querySelectorAll("button, a, [role='button'], div[role='button'], li[role='button'], .picklistOption, .picklistOptionLink"))
                    .filter(el => visible(el) && inWidget(el));
                  for (const el of cands) {
                    const text = norm((el.innerText || "").slice(0, 120));
                    if (!text || text.length > 80) continue;
                    if (!wanted.has(text)) continue;
                    try {
                      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                      if (typeof el.click === "function") el.click();
                      return true;
                    } catch (_) {}
                  }
                  return false;
                }
                """,
                list(wanted),
            )
            if clicked:
                return True
        except Exception:
            continue
    return False

async def click_lenovo_text_locator(page, labels):
    """
    Для Lenovo iframe get_by_text работает стабильнее, чем JS-клик по DOM.
    Используем его как приоритетный fallback на шагах виджета.
    """
    candidates = [(x or "").strip() for x in labels if (x or "").strip()]
    if not candidates:
        return False
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        for label in candidates:
            try:
                locator = frame.get_by_text(label, exact=False).first
                count = await locator.count()
                if count <= 0:
                    continue
                await locator.click(timeout=1200)
                return True
            except Exception:
                continue
    return False

async def click_floating_chat_launcher(page):
    """
    Клик по плавающей кнопке чата в правом нижнем углу (часто синяя круглая кнопка).
    Работает даже когда виджет использует нестандартные классы.
    """
    frames = [page.main_frame] + list(page.frames)
    # Сначала пробуем известные launcher-селекторы чатов (в т.ч. Genesys/Lenovo).
    known_selectors = [
        "button[class*='launcher' i]",
        "[class*='launcher' i] button",
        "[id*='launcher' i]",
        "button[class*='cx' i][class*='launch' i]",
        "[class*='cx-launcher' i]",
        "[class*='chat-button' i]",
        "[aria-label*='open chat' i]",
        "[aria-label*='chat' i]",
    ]
    for frame in frames:
        for sel in known_selectors:
            try:
                el = await frame.query_selector(f"{sel}:visible")
                if el:
                    box = await el.bounding_box()
                    await el.click(force=True)
                    if box:
                        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, delay=30)
                    print(f"  ℹ️  Launcher click via selector: {sel}")
                    return True
            except Exception:
                continue

    for frame in frames:
        try:
            launcher_info = await frame.evaluate(
                """
                () => {
                  const collect = (root, out) => {
                    const nodes = root.querySelectorAll("*");
                    for (const n of nodes) {
                      out.push(n);
                      if (n.shadowRoot) collect(n.shadowRoot, out);
                    }
                  };
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 8 && r.height > 8 && st.visibility !== 'hidden' && st.display !== 'none';
                  };
                  const isChatish = (el) => {
                    const txt = [
                      el.innerText || "",
                      el.getAttribute("aria-label") || "",
                      el.getAttribute("title") || "",
                      el.className || "",
                      el.id || ""
                    ].join(" ").toLowerCase();
                    return /(chat|support|help|message|live)/.test(txt);
                  };
                  const isFloatingBottomRight = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    const posOk = /(fixed|sticky)/.test(st.position || "");
                    const rightZone = r.left > window.innerWidth * 0.55;
                    const bottomZone = r.top > window.innerHeight * 0.45;
                    return posOk && rightZone && bottomZone;
                  };
                  const all = [];
                  collect(document, all);
                  const cands = all
                  .filter((el) => visible(el) && isChatish(el) && isFloatingBottomRight(el))
                  .sort((a, b) => {
                    const ra = a.getBoundingClientRect();
                    const rb = b.getBoundingClientRect();
                    // Предпочитаем самый правый и самый нижний.
                    const sa = (ra.left * 2 + ra.top);
                    const sb = (rb.left * 2 + rb.top);
                    return sb - sa;
                  });
                  const el = cands[0];
                  if (!el) return null;
                  // Ссылки могут уводить страницу — предпочитаем button-like элементы.
                  if (el.tagName.toLowerCase() === "a" && !el.getAttribute("role")) return null;
                  const r = el.getBoundingClientRect();
                  el.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                  el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                  el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                  if (typeof el.click === "function") el.click();
                  return {
                    x: r.left + r.width / 2,
                    y: r.top + r.height / 2,
                    tag: el.tagName,
                    text: ((el.innerText || el.getAttribute("aria-label") || el.getAttribute("title") || "").trim()).slice(0, 80),
                  };
                }
                """
            )
            if launcher_info:
                try:
                    await page.mouse.click(launcher_info["x"], launcher_info["y"], delay=30)
                except Exception:
                    pass
                print(
                    f"  ℹ️  Launcher click via JS fallback: tag={launcher_info['tag']} x={int(launcher_info['x'])} y={int(launcher_info['y'])} text={launcher_info['text']!r}"
                )
                return True
        except Exception:
            continue
    return False

async def keep_page_top(page):
    """Удерживаем страницу вверху (0,0), чтобы бот не уводил экран вниз."""
    try:
        await page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass

async def wait_for_floating_chat_launcher(page, timeout_ms=15000):
    """Ждём появления плавающей кнопки чата (синей кнопки снизу справа)."""
    deadline = time.time() + (timeout_ms / 1000.0)
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        if await click_floating_chat_launcher(page):
            return True
        if await click_floating_chat_hotspot(page):
            print("  ℹ️  Launcher click via hotspot fallback")
            return True
        if attempts in {5, 15, 30}:
            print(f"  ℹ️  Waiting for floating chat launcher... attempt {attempts}")
        await page.wait_for_timeout(400)
    return False

async def click_lenovo_chat_now_bar(page):
    """
    Клик по фиксированной нижней плашке Lenovo "Chat Now" (как на скрине).
    """
    frames = [page.main_frame] + list(page.frames)
    selectors = [
        "button:has-text('Chat Now')",
        "a:has-text('Chat Now')",
        "[aria-label*='Chat Now' i]",
        "[title*='Chat Now' i]",
        "[class*='chat' i]:has-text('Chat Now')",
    ]
    for frame in frames:
        for sel in selectors:
            try:
                el = await frame.query_selector(f"{sel}:visible")
                if el:
                    await el.click(force=True)
                    await page.wait_for_timeout(300)
                    return True
            except Exception:
                continue

    # JS fallback: ищем кликабельный элемент с текстом "chat now" в нижней зоне экрана.
    for frame in frames:
        try:
            clicked = await frame.evaluate(
                """
                () => {
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 20 && r.height > 20 && st.visibility !== 'hidden' && st.display !== 'none';
                  };
                  const bottom = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.top > window.innerHeight * 0.70;
                  };
                  const cands = Array.from(document.querySelectorAll("button, a, [role='button'], div, span"))
                    .filter((el) => visible(el) && bottom(el) && /chat\\s*now/i.test((el.innerText || "") + " " + (el.getAttribute("aria-label") || "")));
                  if (!cands.length) return false;
                  cands[0].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                  return true;
                }
                """
            )
            if clicked:
                await page.wait_for_timeout(300)
                return True
        except Exception:
            continue
    return False

async def wait_for_lenovo_chat_now_bar(page, timeout_ms=45000):
    """
    На Lenovo нижняя плашка Chat Now появляется не сразу.
    Ждём её появления, не выполняя другие действия.
    """
    selectors = [
        "button:has-text('Chat Now')",
        "a:has-text('Chat Now')",
        "[aria-label*='Chat Now' i]",
        "[title*='Chat Now' i]",
        "[class*='chat' i]:has-text('Chat Now')",
    ]
    deadline = time.time() + (timeout_ms / 1000.0)
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            widget_text = await get_lenovo_widget_text(page)
            if widget_text:
                print("  ℹ️  Lenovo widget text already present; skipping Chat Now wait")
                return False
        except Exception:
            pass
        if await is_lenovo_widget_open(page):
            print("  ℹ️  Lenovo widget is already open; skipping Chat Now wait")
            return False
        frames = [page.main_frame] + list(page.frames)
        for frame in frames:
            for sel in selectors:
                try:
                    el = await frame.query_selector(f"{sel}:visible")
                    if el:
                        print(f"  ℹ️  Lenovo Chat Now detected via selector: {sel}")
                        return True
                except Exception:
                    continue
            try:
                found_by_text = await frame.evaluate(
                    """
                    () => {
                      const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 20 && r.height > 20 && st.visibility !== 'hidden' && st.display !== 'none';
                      };
                      return Array.from(document.querySelectorAll("button, a, [role='button'], div, span"))
                        .some(el => visible(el) && /chat\\s*now/i.test((el.innerText || "") + " " + (el.getAttribute("aria-label") || "")));
                    }
                    """
                )
                if found_by_text:
                    print("  ℹ️  Lenovo Chat Now detected via text fallback")
                    return True
            except Exception:
                continue
        if attempts in {10, 30, 60}:
            print(f"  ℹ️  Waiting for Lenovo Chat Now bar... attempt {attempts}")
        await page.wait_for_timeout(500)
    return False

async def get_lenovo_widget_text_snapshot(page):
    """
    Возвращает короткий срез видимого текста Lenovo/Genesys виджета для отладки шагов.
    """
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        try:
            txt = await frame.evaluate(
                """
                () => {
                  const roots = Array.from(document.querySelectorAll(
                    "[class*='cx-widget'], [id*='cx-container'], .cx-webchat, [class*='webchat' i], [class*='genesys' i], #insideChatPane, #inside_holder, .workflowBubble, .picklist"
                  ));
                  const chunks = roots
                    .map((el) => (el.innerText || "").replace(/\\s+/g, " ").trim())
                    .filter(Boolean)
                    .sort((a, b) => b.length - a.length);
                  return chunks[0] || "";
                }
                """
            )
            if txt:
                return txt[:240]
        except Exception:
            continue
    return ""

async def get_lenovo_widget_text(page):
    """
    Возвращает полный видимый текст Lenovo insideChatFrame/виджета.
    """
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        try:
            txt = await frame.evaluate(
                """
                () => {
                  const roots = Array.from(document.querySelectorAll(
                    "#insideChatPane, #inside_holder, #insideChatFrame, [class*='cx-widget'], [id*='cx-container'], .cx-webchat, [class*='webchat' i], [class*='genesys' i], .workflowBubble, .picklist"
                  ));
                  const chunks = roots
                    .map((el) => (el.innerText || "").replace(/\\s+/g, " ").trim())
                    .filter(Boolean)
                    .sort((a, b) => b.length - a.length);
                  return chunks[0] || "";
                }
                """
            )
            if txt:
                return txt
        except Exception:
            continue
        try:
            txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.replace(/\\s+/g, ' ').trim() : ''")
            if txt and "lenovo" in txt.lower():
                return txt
        except Exception:
            continue
    return ""

def classify_lenovo_widget_state(text):
    t = (text or "").lower()
    if not t:
        return "unknown"
    if "start a new chat" in t:
        return "restart"
    if "what's your name" in t or "customer name" in t:
        return "name"
    if "email address" in t or "correct email format" in t:
        return "email"
    if "phone number" in t:
        return "phone"
    if "order number" in t:
        return "order"
    if "would you like to continue with our virtual assistant or speak with an operator" in t:
        return "operator_pick"
    if "retail consumer or a small business" in t:
        return "consumer_pick"
    if "how can we help you today?" in t and "general question" in t:
        return "general_pick"
    if "welcome to lenovo! how can we help you today?" in t and "existing orders" in t:
        return "existing_pick"
    if "how can we help you today?" in t and "type your message here" in t:
        return "chat_ready"
    if "how can we help you today?" in t and "your message" in t and "existing orders" not in t and "general question" not in t:
        return "chat_ready"
    if "how can we help you today?" in t and "(4 of 4)" in t:
        return "order"
    if re.search(r"[a-z0-9][a-z0-9 .,'-]{1,80}, how can we help you today\\?", t) and "existing orders" not in t and "general question" not in t:
        return "chat_ready"
    return "unknown"

async def is_lenovo_widget_open(page):
    """
    Проверка, открыт ли уже Lenovo chat widget (чтобы не кликать launcher повторно и не закрывать его).
    """
    try:
        widget_text = await get_lenovo_widget_text(page)
        if widget_text:
            return True
    except Exception:
        pass
    try:
        visible_iframe = await page.evaluate(
            """
            () => {
              const el = document.querySelector("#insideChatFrame");
              if (!el) return false;
              const r = el.getBoundingClientRect();
              const st = window.getComputedStyle(el);
              return r.width > 40 && r.height > 40 && st.display !== "none" && st.visibility !== "hidden";
            }
            """
        )
        if visible_iframe:
            return True
    except Exception:
        pass
    frames = [page.main_frame] + list(page.frames)
    selectors = [
        "button:has-text('Existing Orders')",
        "button:has-text('General question')",
        "button:has-text('Operator')",
        "button:has-text('Consumer')",
        "textarea[placeholder='Type your message here']",
        ".cx-input textarea",
        "#insideChatFrame",
        "#insideChatPane",
        ".picklistOption",
    ]
    for frame in frames:
        for sel in selectors:
            try:
                el = await frame.query_selector(f"{sel}:visible")
                if el:
                    return True
            except Exception:
                continue
        try:
            found = await frame.evaluate(
                """
                () => {
                  const txt = (document.body && document.body.innerText ? document.body.innerText : "").toLowerCase();
                  return txt.includes("lenovo online sales support")
                    || txt.includes("how can we help you today")
                    || txt.includes("would you like to continue with our virtual assistant");
                }
                """
            )
            if found:
                return True
        except Exception:
            continue
    return False

async def click_floating_chat_hotspot(page):
    """
    Резервный клик по правому нижнему углу, если кнопка чата не находится селектором.
    """
    try:
        vw = page.viewport_size or {"width": 1366, "height": 768}
        w, h = vw["width"], vw["height"]
        points = [
            (w - 46, h - 46),
            (w - 68, h - 68),
            (w - 92, h - 92),
            (w - 120, h - 120),
        ]

        # 1) Натуральные клики мышью по нескольким точкам.
        for x, y in points:
            try:
                await page.mouse.click(x, y, delay=40)
                await page.wait_for_timeout(140)
                await page.mouse.dblclick(x, y, delay=30)
                await page.wait_for_timeout(180)
                if await is_chat_input_ready(page, "lenovo.com"):
                    return True
            except Exception:
                continue

        # 2) Жесткий JS: pointer/touch/click цепочка по elementFromPoint.
        for x, y in points:
            try:
                opened = await page.evaluate(
                    """
                    ({x, y}) => {
                      const fire = (el, type, init = {}) => {
                        try {
                          const evt = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, ...init });
                          el.dispatchEvent(evt);
                        } catch (_) {}
                      };
                      const el = document.elementFromPoint(x, y);
                      if (!el) return false;
                      if (typeof el.focus === "function") el.focus();
                      try {
                        el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, clientX: x, clientY: y, pointerType: "mouse" }));
                        el.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true, clientX: x, clientY: y, pointerType: "mouse" }));
                      } catch (_) {}
                      try {
                        el.dispatchEvent(new TouchEvent("touchstart", { bubbles: true, cancelable: true }));
                        el.dispatchEvent(new TouchEvent("touchend", { bubbles: true, cancelable: true }));
                      } catch (_) {}
                      fire(el, "mousedown");
                      fire(el, "mouseup");
                      fire(el, "click");
                      fire(el, "dblclick");
                      return true;
                    }
                    """,
                    {"x": x, "y": y},
                )
                if opened:
                    await page.wait_for_timeout(220)
                    if await is_chat_input_ready(page, "lenovo.com"):
                        return True
            except Exception:
                continue
    except Exception:
        return False
    return False

async def fill_first_input(page, selectors, value):
    if not value:
        return False
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        for sel in selectors:
            try:
                el = await frame.query_selector(sel)
                if el:
                    await el.click()
                    await el.fill(value)
                    return True
            except Exception:
                continue
    return False

def normalize_customer_name(value):
    return (value or "").strip()

def normalize_customer_email(value):
    return (value or "").strip().lower()

def normalize_customer_phone(value):
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits

def normalize_order_num(value):
    return re.sub(r"\s+", "", (value or "").strip())

async def fill_lenovo_advisor_step(page, session, forced_state=None):
    """
    LenovoAdvisor часто показывает одно поле на шаг.
    Смотрим текст текущего шага и вбиваем соответствующее значение.
    """
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        try:
            txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.lower() : ''")
        except Exception:
            continue

        state = forced_state or classify_lenovo_widget_state(txt)
        target_value = ""
        if state == "name":
            target_value = normalize_customer_name(session.customer_name)
        elif state == "order":
            target_value = normalize_order_num(session.order_num)
        elif state == "email":
            target_value = normalize_customer_email(session.customer_email)
        elif state == "phone":
            target_value = normalize_customer_phone(session.customer_phone)

        if not target_value:
            continue

        selectors_by_state = {
            "name": [
                "#insideWorkflowFieldCell input:visible",
                "input[aria-label*='name' i]:visible",
                "input[placeholder*='name' i]:visible",
                "#insideWorkflowFieldCell textarea:visible",
            ],
            "email": [
                "#insideWorkflowFieldCell input[type='email']:visible",
                "input[aria-label*='email' i]:visible",
                "#insideWorkflowFieldCell input:visible",
                "input[placeholder*='email' i]:visible",
            ],
            "phone": [
                "input[aria-label='xxx-xxx-xxxx']:visible",
                "#insideWorkflowFieldCell input[type='tel']:visible",
                "input[aria-label*='phone' i]:visible",
                "#insideWorkflowFieldCell input:visible",
                "input[placeholder*='phone' i]:visible",
            ],
            "order": [
                "input[aria-label*='order number' i]:visible",
                "#insideWorkflowFieldCell input:visible",
                "input[aria-label*='order' i]:visible",
                "input[placeholder*='order' i]:visible",
            ],
        }

        for sel in selectors_by_state.get(state, []):
            try:
                el = await frame.query_selector(sel)
                if el:
                    try:
                        element_id = (await el.get_attribute("id") or "").strip().lower()
                        aria_label = (await el.get_attribute("aria-label") or "").strip().lower()
                        placeholder = (await el.get_attribute("placeholder") or "").strip().lower()
                        if element_id == "chatinput":
                            continue
                        if state == "email" and "email" not in f"{aria_label} {placeholder}" and "insideworkflowfieldcell" not in sel.lower():
                            continue
                        if state == "phone" and "phone" not in f"{aria_label} {placeholder}" and "insideworkflowfieldcell" not in sel.lower():
                            continue
                        if state == "order" and "order" not in f"{aria_label} {placeholder}" and "insideworkflowfieldcell" not in sel.lower():
                            continue
                    except Exception:
                        pass
                    await el.click(force=True)
                    try:
                        await el.fill("")
                    except Exception:
                        pass
                    try:
                        await el.type(target_value, delay=35)
                    except Exception:
                        await el.fill(target_value)
                    try:
                        await el.dispatch_event("input")
                        await el.dispatch_event("change")
                    except Exception:
                        pass
                    try:
                        await frame.evaluate(
                            """
                            (value) => {
                              const active = document.activeElement;
                              if (!active) return false;
                              active.focus();
                              active.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
                              active.dispatchEvent(new KeyboardEvent("keyup", { key: "End", bubbles: true }));
                              active.dispatchEvent(new InputEvent("input", { bubbles: true, data: value }));
                              active.dispatchEvent(new Event("change", { bubbles: true }));
                              return true;
                            }
                            """,
                            target_value,
                        )
                    except Exception:
                        pass
                    try:
                        print(f"  ✅ Lenovo step: {state} -> {target_value}")
                    except Exception:
                        pass
                    await page.wait_for_timeout(250)
                    try:
                        submitted = await frame.evaluate(
                            """
                            () => {
                              const visible = (el) => {
                                const r = el.getBoundingClientRect();
                                const st = window.getComputedStyle(el);
                                return r.width > 5 && r.height > 5 && st.display !== "none" && st.visibility !== "hidden";
                              };
                              const footer = document.querySelector("#insideChatPaneFooter") || document;
                              const cands = Array.from(footer.querySelectorAll("button, [role='button'], div, span, a"))
                                .filter((el) => visible(el) && el.id !== "chatMenuButton");
                              for (const el of cands) {
                                const sig = [
                                  el.id || "",
                                  el.className || "",
                                  el.getAttribute("aria-label") || "",
                                  el.getAttribute("title") || "",
                                  el.innerText || "",
                                ].join(" ").toLowerCase();
                                if (!/(send|submit|next|continue|arrow|workflow)/.test(sig) && el.id !== "insideSendButton") {
                                  continue;
                                }
                                const r = el.getBoundingClientRect();
                                el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                                el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                                el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                                if (typeof el.click === "function") el.click();
                                return true;
                              }
                              const cells = Array.from(document.querySelectorAll("#insideChatFooterTable td, #insideChatPaneFooter td"))
                                .filter((el) => visible(el) && !/chatMenuButtonCell/i.test(el.id || ""));
                              for (const el of cells) {
                                const r = el.getBoundingClientRect();
                                if (r.width < 4) continue;
                                el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                                el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                                el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                                return true;
                              }
                              return false;
                            }
                            """
                        )
                        if not submitted:
                            await el.press("Enter")
                            await page.wait_for_timeout(250)
                    except Exception:
                        try:
                            await el.press("Enter")
                            await page.wait_for_timeout(250)
                        except Exception:
                            pass
                    return True
            except Exception:
                continue
    return False

async def advance_lenovo_widget_state(page, session):
    """
    Читает текущий prompt Lenovo widget и выбирает следующее действие.
    """
    widget_text = await get_lenovo_widget_text(page)
    state = classify_lenovo_widget_state(widget_text)

    if state == "restart":
        return await restart_expired_lenovo_chat(page)
    if state == "existing_pick":
        if await click_lenovo_picklist_option(page, ["Existing Orders", "Existing order"]):
            print("  ✅ Lenovo step: Existing Orders")
            return True
    if state == "general_pick":
        if await click_lenovo_picklist_option(page, ["General question"]):
            print("  ✅ Lenovo step: General question")
            return True
    if state == "operator_pick":
        if await click_lenovo_picklist_option(page, ["Operator", "Speak with an operator"]):
            print("  ✅ Lenovo step: Operator")
            return True
    if state == "consumer_pick":
        if await click_lenovo_picklist_option(page, ["Consumer"]):
            print("  ✅ Lenovo step: Consumer")
            return True
    if state in {"name", "email", "phone", "order"}:
        return await fill_lenovo_advisor_step(page, session, forced_state=state)
    return False

async def collect_chat_observation(page, store, session):
    widget_text = await get_lenovo_widget_text(page) if store == "lenovo.com" else ""
    state = classify_lenovo_widget_state(widget_text) if widget_text else "unknown"
    operator_open = await is_operator_chat_open(page, store)
    return {
        "store": store,
        "url": page.url,
        "operator_chat_open": operator_open,
        "lenovo_widget_state": state,
        "widget_text_excerpt": (widget_text or "")[:600],
        "last_agent_message": session.last_agent_msg or "",
        "message_count": session.message_count,
        "chat_ready": bool(operator_open and state in {"chat_ready", "unknown"}),
    }

async def advance_lenovo_until_chat_ready(page, session, max_steps=8):
    """
    Проталкивает Lenovo advisor через последовательные workflow-steps
    до обычного chat-ready состояния.
    """
    progressed = False
    for _ in range(max_steps):
        if await is_operator_chat_open(page, "lenovo.com"):
            return True
        widget_text = await get_lenovo_widget_text(page)
        state = classify_lenovo_widget_state(widget_text)
        if state not in {"existing_pick", "general_pick", "operator_pick", "consumer_pick", "name", "email", "phone", "order", "restart"}:
            break
        step_progress = await advance_lenovo_widget_state(page, session)
        if not step_progress:
            break
        progressed = True
        await page.wait_for_timeout(500)
    if progressed and await is_operator_chat_open(page, "lenovo.com"):
        return True
    return False

async def has_lenovo_workflow_input(page):
    """
    Lenovo insideChatFrame может уже быть на workflow-input шаге до появления обычного chat textarea.
    """
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        try:
            el = await frame.query_selector(
                "#insideWorkflowFieldCell input:visible, #insideWorkflowFieldCell textarea:visible, input[aria-label*='(1 of' i]:visible, input[aria-label*='(2 of' i]:visible, input[aria-label*='(3 of' i]:visible, input[aria-label*='(4 of' i]:visible"
            )
            if el:
                return True
        except Exception:
            continue
    return False

async def enrich_session_from_context_pages(page, session):
    """
    Fallback: используем только явно заданные дефолтные данные.
    """
    if not session.customer_name and DEFAULT_CUSTOMER_NAME:
        session.customer_name = DEFAULT_CUSTOMER_NAME
    if not session.customer_email and DEFAULT_CUSTOMER_EMAIL:
        session.customer_email = DEFAULT_CUSTOMER_EMAIL
    if not session.customer_phone and DEFAULT_CUSTOMER_PHONE:
        session.customer_phone = DEFAULT_CUSTOMER_PHONE
    if not session.order_num and DEFAULT_ORDER_NUM:
        session.order_num = DEFAULT_ORDER_NUM

async def enrich_session_from_order_page(page, session):
    """
    Пытается автоматически подтянуть name/order/email со страницы заказа Lenovo.
    Заполняет только пустые поля в session.
    """
    frames = [page.main_frame] + list(page.frames)

    try:
        # Пытаемся взять email из видимого текста страницы.
        if not session.customer_email:
            for frame in frames:
                txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText : ''")
                m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", txt or "")
                if m:
                    session.customer_email = m.group(0)
                    break
    except Exception:
        pass

    try:
        if not session.customer_phone:
            for frame in frames:
                txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText : ''")
                m = re.search(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", txt or "")
                if m:
                    session.customer_phone = re.sub(r"\D+", "", m.group(0))
                    break
    except Exception:
        pass

    try:
        # Пытаемся вытащить order number из текста страницы.
        if not session.order_num:
            patterns = [
                r"(?:Order\\s*(?:Number|No\\.?|#)?\\s*[:#]?\\s*)([A-Z0-9\\-]{6,})",
                r"(?:ecommerceId\\s*[:#]?\\s*)(\\d{6,})",
            ]
            for frame in frames:
                txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText : ''")
                txt = txt or ""
                for pat in patterns:
                    m = re.search(pat, txt, flags=re.I)
                    if m:
                        session.order_num = m.group(1).strip()
                        break
                if session.order_num:
                    break

            # Доп. fallback: взять из URL, если ecommerceId присутствует.
            if not session.order_num:
                m = re.search(r"[?&]ecommerceId=(\\d+)", page.url or "", flags=re.I)
                if m:
                    session.order_num = m.group(1)
    except Exception:
        pass

    try:
        # Имя: сначала пробуем из видимых полей профиля/шапки, потом из e-mail.
        if not session.customer_name:
            for frame in frames:
                for sel in [
                    "[data-testid*='name' i]",
                    "[class*='account' i] [class*='name' i]",
                    "[class*='profile' i] [class*='name' i]",
                    "[aria-label*='name' i]",
                ]:
                    el = await frame.query_selector(sel)
                    if el:
                        txt = (await el.inner_text()).strip()
                        if txt and len(txt) <= 80:
                            session.customer_name = txt
                            break
                if session.customer_name:
                    break

            if not session.customer_name and session.customer_email:
                local = session.customer_email.split("@", 1)[0]
                # Простой fallback из e-mail.
                candidate = re.split(r"[._\\-+]", local)[0]
                session.customer_name = candidate.capitalize() if candidate else session.customer_name
    except Exception:
        pass

async def prepare_chat_for_operator(page, store, session):
    """Автоподготовка pre-chat: выбор раздела и заполнение полей перед подключением оператора."""
    print("🧭 Подготавливаю чат перед подключением оператора...")

    async def safe_wait(ms):
        try:
            await page.wait_for_timeout(ms)
        except Exception:
            return

    try:
        await safe_wait(1200)
    except Exception:
        pass

    # Попытка открыть/развернуть виджет чата
    if store == "lenovo.com":
        await click_lenovo_contact_chat_cta(page)
    await click_first_visible(page, [
        "#contactServiceContainer",
        "#contactBusinessSalesContainer",
        "#or_chat_customer",
        "#or_chat_smb",
        "div:has-text('CHAT WITH US')",
        "a:has-text('CHAT WITH US')",
        "button:has-text('Chat')",
        "button:has-text('Live Chat')",
        "button:has-text('Need help')",
        "button:has-text('Contact us')",
        "[aria-label*='chat' i]",
        "[class*='chat' i] button",
    ])
    await safe_wait(800)

    if store == "lenovo.com":
        # На Lenovo обычно нужно выбрать тип запроса до подключения агента
        if session.order_num:
            await click_first_visible(page, [
                CHAT_SELECTORS["lenovo.com"]["open_chat_existing"],
                "button:has-text('Existing order')",
                "button:has-text('Order support')",
            ])
        else:
            await click_first_visible(page, [
                CHAT_SELECTORS["lenovo.com"]["open_chat_new"],
                "button:has-text('New order')",
                "button:has-text('Pre-sales')",
            ])
        await safe_wait(700)

    # Заполняем pre-chat поля, если они есть
    filled_order = await fill_first_input(page, [
        "input[placeholder*='order' i]",
        "input[name*='order' i]",
        "input[id*='order' i]",
        "textarea[placeholder*='order' i]",
    ], session.order_num)

    filled_name = await fill_first_input(page, [
        "input[placeholder*='name' i]",
        "input[name*='name' i]",
        "input[id*='name' i]",
    ], session.customer_name)

    filled_email = await fill_first_input(page, [
        "input[type='email']",
        "input[placeholder*='email' i]",
        "input[name*='email' i]",
        "input[id*='email' i]",
    ], session.customer_email)
    filled_phone = await fill_first_input(page, [
        "input[type='tel']",
        "input[placeholder*='phone' i]",
        "input[name*='phone' i]",
        "input[id*='phone' i]",
    ], session.customer_phone)
    filled_stepwise = await fill_lenovo_advisor_step(page, session)

    # Переход к оператору/следующему шагу
    clicked_continue = await click_first_visible(page, [
        "button:has-text('Continue')",
        "button:has-text('Start chat')",
        "button:has-text('Start Chat')",
        "button:has-text('Connect')",
        "button:has-text('Submit')",
        "button[type='submit']",
    ])

    if filled_order or filled_name or filled_email or filled_phone or filled_stepwise or clicked_continue:
        print("✅ Pre-chat шаги выполнены (где элементы были найдены).")
    else:
        print("ℹ️  Pre-chat элементы не обнаружены, продолжаю в обычном режиме.")

async def try_open_operator_flow(page, store, session):
    """
    Пытается открыть путь до живого оператора по цепочке:
    1) открыть виджет/контакты
    2) Lenovo: Existing Orders
    3) LenovoAdvisor: General question
    4) LenovoAdvisor: Operator
    5) Continue/Start chat/Connect
    """
    async def safe_wait(ms):
        try:
            await page.wait_for_timeout(ms)
        except Exception:
            return

    await keep_page_top(page)

    async def click_step(selectors, pause_ms=350, step_name=None):
        clicked = await click_first_visible(page, selectors)
        if not clicked:
            # Fallback: глубокий поиск по тексту (shadow DOM / iframe).
            text_hints = []
            for s in selectors:
                m = re.search(r"has-text\\('([^']+)'\\)", s)
                if m:
                    text_hints.append(m.group(1))
            if text_hints:
                clicked = await click_by_text_deep(page, text_hints)
        if clicked:
            if step_name:
                print(f"  ✅ Lenovo step: {step_name}")
            await safe_wait(pause_ms)
        return clicked

    # Для Lenovo сначала открываем виджет, но без повторных кликов, если он уже открыт.
    if store == "lenovo.com":
        progressed = False
        for _ in range(4):
            step_progress = await advance_lenovo_widget_state(page, session)
            if not step_progress:
                break
            progressed = True
            await safe_wait(350)
            if await is_operator_chat_open(page, store):
                return
        widget_open = await is_lenovo_widget_open(page)
        if not widget_open:
            if await click_lenovo_contact_chat_cta(page):
                print("  ✅ Lenovo step: CHAT WITH US")
                for _ in range(4):
                    if not await advance_lenovo_widget_state(page, session):
                        break
                    await safe_wait(350)
                    if await is_operator_chat_open(page, store):
                        return
            direct_chat_clicked = await click_step([
                "#contactServiceContainer",
                "#contactBusinessSalesContainer",
                "#or_chat_customer",
                "#or_chat_smb",
                "div:has-text('CHAT WITH US')",
                "a:has-text('CHAT WITH US')",
            ], pause_ms=700, step_name="CHAT WITH US")
            if direct_chat_clicked:
                for _ in range(4):
                    if not await advance_lenovo_widget_state(page, session):
                        break
                    await safe_wait(350)
                    if await is_operator_chat_open(page, store):
                        return
            bar_ready = await wait_for_lenovo_chat_now_bar(page, timeout_ms=60000)
            if bar_ready and await click_lenovo_chat_now_bar(page):
                print("  ✅ Lenovo step: Chat Now")
                await safe_wait(400)
                for _ in range(4):
                    if not await advance_lenovo_widget_state(page, session):
                        break
                    await safe_wait(350)
                    if await is_operator_chat_open(page, store):
                        return
            if not await is_lenovo_widget_open(page):
                if await wait_for_floating_chat_launcher(page, timeout_ms=15000):
                    print("  ✅ Lenovo step: Chat launcher")
                    await safe_wait(300)
        else:
            print("  ✅ Lenovo step: Widget already open")
            if progressed and await is_operator_chat_open(page, store):
                return
    else:
        # Для остальных магазинов пробуем launcher как раньше.
        if await wait_for_floating_chat_launcher(page, timeout_ms=8000):
            print("  ✅ Lenovo step: Chat launcher")
        await safe_wait(250)

    # Общие кнопки открытия помощи/чата.
    # Для Lenovo этот шаг может мешать (переключает виджет не туда), поэтому пропускаем.
    if store != "lenovo.com":
        await click_step([
            "button:has-text('Contact us')",
            "a:has-text('Contact us')",
            "button:has-text('Support')",
            "a:has-text('Support')",
            "button:has-text('Chat')",
            "a:has-text('Chat')",
            "button:has-text('Live Chat')",
            "a:has-text('Live Chat')",
            "button:has-text('Need help')",
            "a:has-text('Need help')",
            "[aria-label*='chat' i]",
            "[class*='chat' i] button",
        ], pause_ms=500, step_name="Contact/Support entry")

    if store == "lenovo.com":
        # Для кейсов по проблемам с доставкой/возвратом всегда идём через Existing Orders.
        existing_clicked = await click_step([
            "button:has-text('Existing Orders')",
            "button:has-text('Existing order')",
            "button:has-text('Order support')",
            "a:has-text('Existing Orders')",
            "a:has-text('Existing order')",
        ], pause_ms=500, step_name="Existing Orders")
        existing_exact = await click_lenovo_button_exact(page, ["Existing Orders", "Existing order"])
        if not existing_exact:
            existing_exact = await click_lenovo_picklist_option(page, ["Existing Orders", "Existing order"])
        if existing_exact:
            print("  ✅ Lenovo step: Existing Orders")
        existing_any = existing_clicked or existing_exact

        # В окне LenovoAdvisor выбираем раздел General question.
        general_clicked = await click_step([
            "button:has-text('General question')",
            "a:has-text('General question')",
            "div[role='button']:has-text('General question')",
            "li:has-text('General question')",
        ], pause_ms=350, step_name="General question")
        if not general_clicked:
            general_clicked = await click_lenovo_button_exact(page, ["General question"])
        if not general_clicked:
            general_clicked = await click_lenovo_picklist_option(page, ["General question"])
        if general_clicked:
            print("  ✅ Lenovo step: General question")
        if not general_clicked:
            await click_lenovo_option(page, "General question")

        # Следующий шаг LenovoAdvisor: переключаемся на живого оператора.
        operator_clicked = await click_step([
            "button:has-text('Operator')",
            "a:has-text('Operator')",
            "div[role='button']:has-text('Operator')",
            "li:has-text('Operator')",
        ], pause_ms=400, step_name="Operator")
        if not operator_clicked:
            operator_clicked = await click_lenovo_button_exact(page, ["Operator"])
        if not operator_clicked:
            operator_clicked = await click_lenovo_picklist_option(page, ["Operator", "Speak with an operator"])
        if operator_clicked:
            print("  ✅ Lenovo step: Operator")
        if not operator_clicked:
            await click_lenovo_option(page, "Operator")
        # Жесткий приоритет именно для экрана "Virtual Assistant vs Operator".
        await click_by_text_deep(page, [
            "speak with an operator",
            "operator",
        ])
        await safe_wait(350)

        # Шаг LenovoAdvisor: Consumer vs Small Business.
        consumer_clicked = await click_step([
            "button:has-text('Consumer')",
            "a:has-text('Consumer')",
            "div[role='button']:has-text('Consumer')",
            "li:has-text('Consumer')",
        ], pause_ms=350, step_name="Consumer")
        if not consumer_clicked:
            consumer_clicked = await click_lenovo_button_exact(page, ["Consumer"])
        if not consumer_clicked:
            consumer_clicked = await click_lenovo_picklist_option(page, ["Consumer"])
        if consumer_clicked:
            print("  ✅ Lenovo step: Consumer")
        if not consumer_clicked:
            await click_lenovo_option(page, "Consumer")

        if not existing_any and not general_clicked and not operator_clicked and not consumer_clicked:
            snapshot = await get_lenovo_widget_text_snapshot(page)
            if snapshot:
                print(f"  ℹ️  Lenovo widget snapshot: {snapshot!r}")
            else:
                print("  ℹ️  Lenovo widget snapshot: no widget text captured")

        # Автозаполнение шагов LenovoAdvisor (name/order/email), если поля присутствуют.
        await fill_first_input(page, [
            "input[placeholder*='name' i]",
            "input[name*='name' i]",
            "input[id*='name' i]",
            "textarea[placeholder*='name' i]",
        ], session.customer_name)
        await fill_first_input(page, [
            "input[placeholder*='order' i]",
            "input[name*='order' i]",
            "input[id*='order' i]",
            "textarea[placeholder*='order' i]",
        ], session.order_num)
        await fill_first_input(page, [
            "input[type='email']",
            "input[placeholder*='email' i]",
            "input[name*='email' i]",
            "input[id*='email' i]",
        ], session.customer_email)
        await fill_first_input(page, [
            "input[type='tel']",
            "input[placeholder*='phone' i]",
            "input[name*='phone' i]",
            "input[id*='phone' i]",
        ], session.customer_phone)
        await fill_lenovo_advisor_step(page, session)

    await click_step([
        "button:has-text('Continue')",
        "button:has-text('Start chat')",
        "button:has-text('Start Chat')",
        "button:has-text('Chat with operator')",
        "button:has-text('Talk to an operator')",
        "button:has-text('Connect')",
        "button:has-text('Submit')",
        "button:has-text('Chat now')",
        "button:has-text('Start messaging')",
        "button[type='submit']",
    ], pause_ms=300, step_name="Continue/Start chat")

    await keep_page_top(page)

async def is_chat_offline(page):
    """Грубая проверка офлайна/недоступности live chat."""
    patterns = [
        "chat is unavailable",
        "no agents available",
        "all agents are busy",
        "outside business hours",
        "offline",
        "currently unavailable",
    ]
    frames = [page.main_frame] + list(page.frames)
    for frame in frames:
        try:
            txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.toLowerCase() : ''")
            if any(p in txt for p in patterns):
                return True
        except Exception:
            continue
    return False

async def is_chat_input_ready(page, store):
    """Проверяет, доступно ли поле ввода чата (оператор подключен/чат открыт)."""
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        frames = [page.main_frame] + list(page.frames)
        if store == "lenovo.com":
            widget_text = (await get_lenovo_widget_text(page)).lower()
            if "how can we help you today?" in widget_text and "existing orders" not in widget_text and "general question" not in widget_text:
                return True
            for frame in frames:
                # Для Lenovo требуем признаки Genesys-виджета, а не любое поле на странице.
                widget = await frame.query_selector(
                    "[class*='cx-widget'], [id*='cx-container'], .cx-webchat, [class*='webchat' i], [class*='genesys' i], #insideChatPane, #inside_holder"
                )
                if not widget:
                    continue
                chat_input = await frame.query_selector(
                    "textarea[placeholder='Type your message here'], #chatInput, .cx-input textarea, [class*='cx-input'] textarea, [contenteditable='true']"
                )
                if chat_input:
                    return True
            return False

        ready_selectors = [
            sel["input"],
            "textarea[placeholder*='message' i]",
            "textarea[placeholder*='type' i]",
            "textarea",
            "input[placeholder*='message' i]",
            "input[type='text']",
        ]
        for frame in frames:
            for input_sel in ready_selectors:
                el = await frame.query_selector(input_sel)
                if el:
                    return True
        return False
    except Exception:
        return False

async def is_operator_chat_open(page, store):
    """Более строгая проверка, что открыт именно чат с оператором."""
    frames = [page.main_frame] + list(page.frames)
    if store == "lenovo.com":
        widget_text = (await get_lenovo_widget_text(page)).lower()
        widget_state = classify_lenovo_widget_state(widget_text)
        if widget_state == "chat_ready":
            return True
        for frame in frames:
            try:
                has_widget = await frame.query_selector(
                    "[class*='cx-widget'], [id*='cx-container'], .cx-webchat, [class*='webchat' i], [class*='genesys' i], #insideChatPane, #inside_holder"
                )
                has_chat_input = await frame.query_selector(
                    "textarea[placeholder='Type your message here'], #chatInput, .cx-input textarea, [class*='cx-input'] textarea, [contenteditable='true'], #insideWorkflowFieldCell input[aria-label*='how can we help you today' i], #insideWorkflowFieldCell textarea[aria-label*='how can we help you today' i]"
                )
                if has_widget and has_chat_input and widget_state in {"chat_ready", "unknown"}:
                    return True
            except Exception:
                continue
        return False
    return await is_chat_input_ready(page, store)

async def wait_until_chat_ready(context, page, store, session, preferred_domain=None, timeout_sec=180):
    """
    Ждём готовности чата после pre-chat.
    Пока чат не готов, периодически дожимаем кнопки Continue/Start chat.
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        # Если текущая страница закрылась/перезагрузилась, выбираем новую релевантную.
        try:
            _ = page.url
        except Exception:
            try:
                best = await pick_best_page(context, preferred_domain=preferred_domain)
                if best:
                    page = best
                    store = get_store_name(page.url)
            except Exception:
                await asyncio.sleep(1)
                continue

        # Перед каждой проверкой выбираем самую релевантную вкладку.
        try:
            best = await pick_best_page(context, preferred_domain=preferred_domain)
            if best and best != page:
                page = best
                store = get_store_name(page.url)
        except Exception:
            pass

        if await is_chat_input_ready(page, store):
            return True, page, store

        if store == "lenovo.com":
            try:
                if await advance_lenovo_until_chat_ready(page, session, max_steps=8):
                    return True, page, store
            except Exception:
                pass

        # Дожимаем путь до оператора на каждом цикле.
        if ticks := int((time.time() - (deadline - timeout_sec))):
            if ticks % 5 == 0:
                print(f"  …пытаюсь открыть чат/оператора ({ticks}s)")
        await try_open_operator_flow(page, store, session)

        # Если чат офлайн, просто ждём и повторяем попытки.
        if await is_chat_offline(page):
            try:
                await page.wait_for_timeout(3000)
            except Exception:
                await asyncio.sleep(1)
            continue

        try:
            await click_first_visible(page, [
                "button:has-text('Continue')",
                "button:has-text('Start chat')",
                "button:has-text('Start Chat')",
                "button:has-text('Connect')",
                "button:has-text('Submit')",
                "button:has-text('Next')",
                "button[type='submit']",
            ])
            await page.wait_for_timeout(1500)
        except Exception:
            # Страница могла закрыться прямо в момент клика/таймаута — продолжаем цикл.
            await asyncio.sleep(1)
            continue
    return False, page, store

def is_critical_message(text, message_count):
    """Определяет, нужно ли согласование перед отправкой."""
    if message_count >= 3:
        return True
    lowered = text.lower()
    critical_markers = [
        "chargeback",
        "fcba",
        "ftc",
        "formal dispute",
        "final notice",
        "legal",
        "deadline",
        "supervisor",
        "escalation",
    ]
    return any(marker in lowered for marker in critical_markers)

def read_console_command():
    """Неблокирующее чтение команды из консоли (macOS/Linux)."""
    try:
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.readline().strip()
    except Exception:
        return None
    return None


# ─── MAIN FLOW ───────────────────────────────────────────────────────────────

def print_banner():
    print("\n" + "═" * 55)
    print("  ⚖  SUPPORT COPILOT — Dolphin Anty Edition")
    print("  US E-Commerce Legal Assistant")
    print("═" * 55)

def ask(prompt, default=""):
    val = input(f"  {prompt}: ").strip()
    return val if val else default

def read_multiline_block(prompt):
    print(f"  {prompt} (заверши пустой строкой):")
    lines = []
    while True:
        line = input().rstrip("\n")
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines).strip()

def parse_customer_block(raw_text):
    """
    Поддерживает оба формата:
    1) key: value
    2) key (строка) + value (следующая строка)
    """
    data = {
        "name": "",
        "order": "",
        "email": "",
        "phone": "",
        "date": "",
    }
    if not raw_text:
        return data

    def norm(s):
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    def detect_key(s):
        t = norm(s)
        if any(k in t for k in ["customer name", "name", "имя"]):
            return "name"
        if any(k in t for k in ["order number", "order #", "order", "номер заказа"]):
            return "order"
        if any(k in t for k in ["email", "e-mail", "почта"]):
            return "email"
        if any(k in t for k in ["phone number", "phone", "телефон"]):
            return "phone"
        if any(k in t for k in ["date placed", "order date", "дата"]):
            return "date"
        return None

    pending = None
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # profile: ... пропускаем
        if norm(line).startswith("profile:"):
            continue

        # Формат key: value
        if ":" in line:
            k, v = line.split(":", 1)
            key = detect_key(k)
            value = v.strip()
            if key and value:
                data[key] = value
                pending = None
                continue

        # Формат key\nvalue
        key_on_line = detect_key(line)
        if key_on_line:
            pending = key_on_line
            continue
        if pending:
            data[pending] = line
            pending = None

    # Нормализация телефона (оставляем только + и цифры)
    if data["phone"]:
        phone = data["phone"].strip()
        if phone.startswith("+"):
            data["phone"] = "+" + re.sub(r"\D+", "", phone)
        else:
            data["phone"] = re.sub(r"\D+", "", phone)
    return data

def choose_profile(profiles):
    print("\n📋 Доступные профили Dolphin Anty:\n")
    for i, p in enumerate(profiles):
        print(f"  [{i+1}] {p.get('name', 'Без имени')}  (ID: {p['id']})")
    print()
    while True:
        try:
            idx = int(input("  Выбери номер профиля: ")) - 1
            if 0 <= idx < len(profiles):
                return profiles[idx]
        except ValueError:
            pass
        print("  Неверный номер, попробуй снова.")

async def run_session(
    profile_id,
    cdp_port,
    session,
    auto_send_noncritical=False,
    auto_send_critical=False,
    force_auto_mode=False,
    prechat_only=False,
):
    ws_url = f"ws://localhost:{cdp_port}"
    # Для Dolphin порт automation часто требует путь /devtools/browser/<id>.
    # Получаем корректный WS endpoint через стандартный CDP /json/version.
    try:
        info = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=5).json()
        ws_from_version = info.get("webSocketDebuggerUrl")
        if ws_from_version:
            ws_url = ws_from_version
    except Exception:
        pass
    print(f"\n🔌 Подключаюсь к профилю на порту {cdp_port}...")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        pages = context.pages
        preferred_domain = preferred_store_domain(session.store)
        page = await pick_best_page(context, preferred_domain=preferred_domain) if pages else await context.new_page()
        if not page:
            page = await context.new_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass

        # Для Lenovo фиксируем стартовую страницу на нужный entrypoint с chat widget.
        if preferred_domain == "lenovo.com":
            current = (page.url or "").lower()
            if "lenovo.com/us/vipmembers/ticketsatwork/en/contact/order-support" not in current:
                try:
                    print(f"↪️  Открываю Lenovo chat page: {LENOVO_CHAT_URL}")
                    await page.goto(LENOVO_CHAT_URL, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1200)
                except Exception as e:
                    print(f"⚠️  Не удалось открыть целевую Lenovo страницу: {e}")

        url = page.url
        store = get_store_name(url)
        print(f"🌐 Страница: {url}")
        print(f"🏪 Магазин: {store}\n")
        await enrich_session_from_order_page(page, session)
        await enrich_session_from_context_pages(page, session)
        if session.customer_name:
            print(f"🪪 Автоданные: name={session.customer_name}")
        if session.order_num:
            print(f"🧾 Автоданные: order={session.order_num}")
        if session.customer_email:
            print(f"📧 Автоданные: email={session.customer_email}")
        if session.customer_phone:
            print(f"☎️  Автоданные: phone={session.customer_phone}")
        await prepare_chat_for_operator(page, store, session)
        if prechat_only:
            print("🛠 Режим pre-chat only: прохожу pre-chat и останавливаюсь до онлайн-диалога.")
            for _ in range(10):
                await try_open_operator_flow(page, store, session)
                await asyncio.sleep(1.2)
            print("✅ Pre-chat прогон завершён. Остановка до подключения оператора.")
            await browser.close()
            return
        print("⏳ Жду подключения оператора/готовности окна чата...")
        ready, page, store = await wait_until_chat_ready(
            context,
            page,
            store,
            session,
            preferred_domain=preferred_domain,
            timeout_sec=240,
        )
        while not ready:
            print("⏳ Чат ещё не готов, продолжаю авто-подключение к оператору...")
            ready, page, store = await wait_until_chat_ready(
                context,
                page,
                store,
                session,
                preferred_domain=preferred_domain,
                timeout_sec=180,
            )
        print("✅ Чат готов, запускаю диалог.")

        # Пользователь может перейти на другую вкладку/домен уже после старта.
        # Перед работой с селекторами пересчитываем магазин по текущему URL.
        try:
            current_url = page.url
            current_store = get_store_name(current_url)
            if current_store != store:
                store = current_store
                session.store = current_store
                print(f"🔄 Обновил магазин по текущей вкладке: {store}")
        except Exception:
            pass

        # Генерируем первое сообщение
        print("⚖️  Генерирую первое сообщение...")
        first_observation = await collect_chat_observation(page, store, session)
        first_plan = session.plan_next_action(
            agent_text="",
            observation=first_observation,
            first_turn=True,
        )
        print(f"🧠 Agent action: {first_plan['action']} ({first_plan['reason'] or 'no reason'})")
        first_msg = (first_plan.get("message") or "").strip()
        if first_plan["action"] != "send_message" or not first_msg:
            first_msg = session.generate_first_message()
        else:
            session.history.append({"role": "assistant", "content": first_msg})
            session.transcript.append({"role": "customer_rep", "content": first_msg})
            session.message_count = 1
        if not first_msg.strip():
            first_msg = (
                "Hello, I need support with my order. "
                "I am requesting a concrete resolution today: either a full refund or a replacement. "
                "Please confirm the next step and timeline."
            )

        print("\n" + "─" * 55)
        print("📤 ПЕРВОЕ СООБЩЕНИЕ (для отправки агенту):")
        print("─" * 55)
        print(first_msg)
        print("─" * 55)

        if not session.should_send_message(first_msg):
            print("⏭️  Дубликат первого сообщения обнаружен, пропускаю отправку.")
            first_msg = ""

        if first_msg and not await is_operator_chat_open(page, store):
            print("⚠️  Операторский чат ещё не открыт. Возвращаюсь к шагам подключения.")
            await try_open_operator_flow(page, store, session)
            await page.wait_for_timeout(1200)
            if not await is_operator_chat_open(page, store):
                print("❌ Чат оператора не подтверждён. Ожидаю открытия чата и не отправляю сообщение.")
                while not await is_operator_chat_open(page, store):
                    await try_open_operator_flow(page, store, session)
                    await page.wait_for_timeout(1500)

        # Вставляем в чат
        typed = False
        if first_msg:
            typed = await type_message(page, store, first_msg)
            if typed:
                print("✅ Текст вставлен в поле чата")
            else:
                print("⚠️  Не удалось вставить автоматически — скопируй вручную")

        first_is_critical = is_critical_message(first_msg, session.message_count) if first_msg else False
        should_confirm_first = (
            (first_is_critical and not auto_send_critical) or (not first_is_critical and not auto_send_noncritical)
        ) if first_msg else False
        if should_confirm_first:
            confirm = input("\n  Отправить сообщение? [Y/n]: ").strip().lower()
            allow_send = confirm != "n"
        else:
            allow_send = bool(first_msg)
            if first_msg:
                first_mode = "критичный шаг" if first_is_critical else "не критичный шаг"
                print(f"🤖 Авто-отправка: {first_mode}, отправляю без подтверждения.")

        if allow_send:
            await human_send_delay(first_msg)
            sent = await send_message(page, store)
            print("✅ Отправлено!" if sent else "⚠️  Нажми Enter в чате вручную")
            if sent:
                session.mark_message_sent(first_msg)

        # Основной цикл диалога
        print("\n🔄 Онлайн-режим диалога запущен.\n")

        if force_auto_mode:
            use_auto = True
            print("🤖 Автопилот: авто-режим включён.")
        else:
            auto_mode = input("  Авто-режим? (читаю чат каждые 5 сек) [Y/n]: ").strip().lower()
            use_auto = auto_mode != "n"

        while True:
            if use_auto:
                print("  👁  Слежу за чатом... (q = выход, m = ввести ответ агента вручную)")
                agent_msg = None
                ticks = 0
                while not agent_msg:
                    await asyncio.sleep(1)
                    ticks += 1

                    cmd = read_console_command()
                    if cmd:
                        cmd_low = cmd.lower()
                        if cmd_low in {"q", "quit", "exit"}:
                            return
                        if cmd_low == "m":
                            manual = input("  Вставь ответ агента вручную: ").strip()
                            if manual:
                                agent_msg = manual
                                break

                    if ticks % 5 == 0:
                        detected = await read_last_agent_message(page, store)
                        if detected and detected != session.last_agent_msg:
                            agent_msg = detected
                            session.last_agent_msg = detected
                            print(f"\n💬 АГЕНТ: {agent_msg}")
            else:
                agent_msg = input("\n  Вставь ответ агента: ").strip()
                if agent_msg.lower() == "q":
                    break

            if not agent_msg:
                continue

            print("\n⚖️  Генерирую ответ...")
            observation = await collect_chat_observation(page, store, session)
            plan = session.plan_next_action(
                agent_text=agent_msg,
                observation=observation,
                first_turn=False,
            )
            print(f"🧠 Agent action: {plan['action']} ({plan['reason'] or 'no reason'})")
            if plan["action"] == "finish":
                print("🏁 Агентный контур завершил кейс.")
                break
            if plan["action"] == "wait":
                print("⏳ Агент решил подождать следующий шаг/ответ без отправки сообщения.")
                continue
            our_reply = (plan.get("message") or "").strip()
            if not our_reply:
                our_reply = session.generate_reply(agent_msg)
            else:
                session.record_agent_message(agent_msg)
                session.history.append({
                    "role": "user",
                    "content": f'Agent replied: "{agent_msg}"\nObservation: {json.dumps(observation, ensure_ascii=True)}',
                })
                session.history.append({"role": "assistant", "content": our_reply})
                session.transcript.append({"role": "customer_rep", "content": our_reply})
                session.message_count += 1
            if not (our_reply or "").strip():
                our_reply = (
                    "I need this resolved now. Please provide a case ID and confirm either a full refund "
                    "or a replacement timeline within 48 hours."
                )

            print("\n" + "─" * 55)
            step_labels = {1: "ЭСКАЛАЦИЯ 2 — Требование", 2: "ЭСКАЛАЦИЯ 3 — Юридическое давление", 3: "ФИНАЛЬНОЕ ТРЕБОВАНИЕ"}
            label = step_labels.get(session.message_count - 1, f"СООБЩЕНИЕ {session.message_count}")
            print(f"📤 {label}:")
            print("─" * 55)
            print(our_reply)
            print("─" * 55)

            if not session.should_send_message(our_reply):
                print("⏭️  Дубликат ответа обнаружен, пропускаю отправку.")
                continue

            if not await is_operator_chat_open(page, store):
                print("⚠️  Операторский чат не подтверждён. Пропускаю отправку и пытаюсь открыть чат.")
                await try_open_operator_flow(page, store, session)
                continue

            typed = await type_message(page, store, our_reply)
            if typed:
                print("✅ Текст вставлен в поле чата")
            else:
                print("⚠️  Скопируй и вставь вручную")

            critical = is_critical_message(our_reply, session.message_count)
            should_confirm = (critical and not auto_send_critical) or (
                not critical and not auto_send_noncritical
            )
            if should_confirm:
                reason = "критичный шаг" if critical else "ручной режим"
                confirm = input(f"\n  Отправить? [Y/n] ({reason}): ").strip().lower()
                allow_send = confirm != "n"
            else:
                mode = "критичный шаг" if critical else "не критичный шаг"
                print(f"🤖 Авто-отправка: {mode}, отправляю без подтверждения.")
                allow_send = True

            if allow_send:
                await human_send_delay(our_reply)
                sent = await send_message(page, store)
                print("✅ Отправлено!" if sent else "⚠️  Нажми Enter в чате вручную")
                if sent:
                    session.mark_message_sent(our_reply)

            if session.message_count >= 4:
                print("\n🏁 Достигнут финальный этап эскалации.")
                cont = input("  Продолжить диалог? [y/N]: ").strip().lower()
                if cont != "y":
                    break

        print("\n✅ Сессия завершена.")
        await browser.close()


def detect_store_from_profile_name(name):
    """Определяем магазин из названия профиля"""
    name_lower = name.lower()
    if "amazon" in name_lower:       return "Amazon"
    if "lenovo" in name_lower:       return "Lenovo.com"
    if "zara" in name_lower:         return "Zara.com"
    if "walmart" in name_lower:      return "Walmart"
    if "ebay" in name_lower:         return "eBay"
    return None

def detect_case_from_profile_name(name):
    """Определяем тип кейса из названия профиля"""
    name_lower = name.lower()
    if any(k in name_lower for k in ["doa", "damaged", "broken", "defective", "screen", "replacement"]):
        return "DOA"
    if "inr" in name_lower or "not received" in name_lower or "не получил" in name_lower:
        return "INR"
    if "rnr" in name_lower or "refund" in name_lower or "возврат" in name_lower:
        return "RNR"
    return None


async def main():
    print_banner()

    global OPENAI_API_KEY, OPENAI_MODEL, DOLPHIN_SESSION_TOKEN, DOLPHIN_CLOUD_API_KEY
    if not OPENAI_API_KEY:
        key = ask("OpenAI API Key (sk-...)")
        os.environ["OPENAI_API_KEY"] = key
        OPENAI_API_KEY = key

    if not OPENAI_MODEL:
        model = ask("OpenAI model", "gpt-4.1-mini")
        OPENAI_MODEL = model

    if not DOLPHIN_SESSION_TOKEN:
        token = ask("Dolphin Session Token (если требуется, иначе Enter)", "")
        if token:
            os.environ["DOLPHIN_SESSION_TOKEN"] = token
            DOLPHIN_SESSION_TOKEN = token

    if not DOLPHIN_CLOUD_API_KEY:
        cloud_key = ask("Dolphin{cloud} API-ключ (если есть, иначе Enter)", "")
        if cloud_key:
            os.environ["DOLPHIN_CLOUD_API_KEY"] = cloud_key
            DOLPHIN_CLOUD_API_KEY = cloud_key

    # ── Ввод имени профиля ──────────────────────────────────────────────────
    print()
    autopilot = ask("Режим автопилота (минимум вопросов)? [Y/n]", "y").lower() != "n"

    profile_name = ask("Введи название профиля Dolphin Anty")
    if not profile_name:
        print("❌ Название не введено.")
        sys.exit(1)

    print(f"\n🐬 Ищу и запускаю профиль «{profile_name}»...")
    profile_running_without_automation = False
    public_started = start_profile_public_by_name(profile_name) if ALLOW_TEMP_PROFILE_START else None
    if public_started:
        profile_id = public_started["profile_id"]
        cdp_port = public_started["port"]
        full_name = profile_name
        print(f"✅ Профиль запущен через публичный Local API (порт {cdp_port})")
    else:
        real_id = extract_profile_id_from_logs(profile_name)
        if real_id:
            started_by_real_id = start_profile_public_by_id(real_id)
            if started_by_real_id:
                profile_id = started_by_real_id["profile_id"]
                cdp_port = started_by_real_id["port"]
                full_name = profile_name
                if cdp_port:
                    print(f"✅ Профиль запущен через real profile_id из логов (порт {cdp_port})")
                else:
                    print("ℹ️  Профиль уже запущен; перезапуск отключен (ALLOW_PROFILE_RESTART=0).")
                    profile_running_without_automation = True
            else:
                real_id = None
        if not real_id:
            if ALLOW_PROFILE_RESTART:
                restarted = restart_running_profile_for_automation()
                if restarted:
                    profile_id = restarted["profile_id"]
                    cdp_port = restarted["port"]
                    full_name = profile_name
                    print(f"✅ Подключение через fallback: перезапущен запущенный профиль (порт {cdp_port})")
                    real_id = "fallback"
            if not real_id:
                print("ℹ️  Публичный запуск не сработал, пробую стандартный путь через session token...")
                profile = find_profile_by_name(profile_name)
                if not profile:
                    print(f"❌ Профиль «{profile_name}» не найден.")
                    all_profiles = list_profiles()
                    if all_profiles:
                        print("\nДоступные профили:")
                        for p in all_profiles:
                            print(f"  • {p.get('name')}")
                    sys.exit(1)
                profile_id = profile["id"]
                full_name = profile.get("name", profile_name)
                cdp_port = None
                print(f"✅ Найден: «{full_name}»")

    # ── Авто-определение из имени профиля ───────────────────────────────────
    parsed = parse_profile_name(full_name)
    client_name = parsed["client"]
    auto_store  = parsed["store"]
    auto_case   = parsed["case_type"]

    print(f"\n📋 ДАННЫЕ КЕЙСА  (клиент: {client_name})\n")

    if auto_store:
        print(f"  🏪 Магазин: {auto_store}  (из имени профиля)")
        store = auto_store
    else:
        store = "Lenovo.com" if autopilot else ask("Магазин (Amazon / Lenovo.com / Zara.com / другой)", "Lenovo.com")

    if auto_case:
        print(f"  📂 Тип кейса: {auto_case}  (из имени профиля)")
        case_type = auto_case
    else:
        case_type = ("INR" if autopilot else ask("Тип [INR = не получил товар / RNR = не вернули деньги]", "INR")).upper()

    use_block = ask("Вставить данные клиента блоком? [Y/n]", "y").lower() != "n"
    block_data = {}
    if use_block:
        raw_block = read_multiline_block("Вставь блок данных (name/order/email/phone)")
        block_data = parse_customer_block(raw_block)
        if block_data.get("name"):
            print(f"  🪪 Name: {block_data['name']}")
        if block_data.get("order"):
            print(f"  🧾 Order: {block_data['order']}")
        if block_data.get("email"):
            print(f"  📧 Email: {block_data['email']}")
        if block_data.get("phone"):
            print(f"  ☎️  Phone: {block_data['phone']}")

    order_num = block_data.get("order") or ask("Номер заказа (Enter = пропустить)", "")
    amount = ask("Сумма в $ (Enter = пропустить)", "")
    details = ask("Детали проблемы", "") if not autopilot else ""
    details_lower = (details or "").lower()
    if any(k in details_lower for k in ["broken screen", "broken", "defective", "damaged", "replacement", "returned back", "returned to lenovo", "ups label"]):
        case_type = "DOA"
    customer_name = block_data.get("name") or ask("Имя клиента для pre-chat (Enter = клиент из профиля)", client_name)
    customer_email = block_data.get("email") or ask("Email для pre-chat (Enter = пропустить)", "")
    customer_phone = block_data.get("phone") or ask("Phone для pre-chat (Enter = пропустить)", "")
    if DEFAULT_CUSTOMER_NAME and customer_name == client_name:
        customer_name = DEFAULT_CUSTOMER_NAME
    customer_email = normalize_customer_email(customer_email or DEFAULT_CUSTOMER_EMAIL)
    customer_phone = normalize_customer_phone(customer_phone or DEFAULT_CUSTOMER_PHONE)
    order_num = normalize_order_num(order_num or DEFAULT_ORDER_NUM)
    if customer_phone and len(re.sub(r"\D+", "", customer_phone)) < 7:
        customer_phone = ""
    prechat_only = ask("Режим pre-chat only (без онлайн-диалога)? [y/N]", "n").lower() == "y"

    if profile_running_without_automation and not cdp_port:
        print("\n❌ Этот профиль уже открыт без automation-порта.")
        print("   Чтобы избежать закрытия/перезапуска окна, бот не будет его трогать.")
        print("   Действия:")
        print("   1) Закрой профиль в Dolphin вручную.")
        print("   2) Запусти bot.py снова — бот поднимет профиль сам без temporary IDs.")
        print("   (Либо включи ALLOW_PROFILE_RESTART=1, если согласен на stop/start.)")
        sys.exit(1)

    # ── Запуск профиля (если ещё не запущен в public path) ──────────────────
    if not cdp_port:
        print(f"\n▶️  Запускаю профиль «{full_name}»...")
        cdp_port = start_profile(profile_id)
        if not cdp_port:
            print("❌ Не удалось запустить профиль.")
            sys.exit(1)
        print(f"✅ Профиль запущен на порту {cdp_port}")
        await asyncio.sleep(2)

    session = CopilotSession(
        store, case_type, order_num, amount, details,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
    )

    try:
        if autopilot:
            auto_send_noncritical = True
            auto_send_critical = True
            force_auto_mode = True
            print("🤖 Автопилот активен: авто-режим + авто-отправка всех шагов.")
        else:
            auto_send_noncritical = ask("Авто-отправка не критичных шагов? [y/N]", "n").lower() == "y"
            auto_send_critical = ask("Авто-отправка критичных шагов? [y/N]", "n").lower() == "y"
            force_auto_mode = False

        await run_session(
            profile_id,
            cdp_port,
            session,
            auto_send_noncritical=auto_send_noncritical,
            auto_send_critical=auto_send_critical,
            force_auto_mode=force_auto_mode,
            prechat_only=prechat_only,
        )
    finally:
        if KEEP_PROFILE_OPEN:
            print("🟢 Профиль оставлен открытым (KEEP_PROFILE_OPEN=1).")
        else:
            stop_profile(profile_id)
            print("🔴 Профиль остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
