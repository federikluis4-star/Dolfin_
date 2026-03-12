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
from collections import Counter
from pathlib import Path
import requests
from datetime import datetime
from playwright.async_api import async_playwright

# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent

def resolve_project_path(raw_path):
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path

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
RUN_CONFIG_ENV = "SUPPORT_COPILOT_RUN_CONFIG_JSON"
UI_SESSION_ENV = "SUPPORT_COPILOT_UI_SESSION_ID"
UI_COMMAND_QUEUE_ENV = "SUPPORT_COPILOT_UI_COMMAND_QUEUE_PATH"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DOLPHIN_SESSION_TOKEN = os.getenv("DOLPHIN_SESSION_TOKEN", "")
DOLPHIN_CLOUD_API_KEY = os.getenv("DOLPHIN_CLOUD_API_KEY", "")
DEFAULT_CUSTOMER_EMAIL = os.getenv("DEFAULT_CUSTOMER_EMAIL", "")
DEFAULT_CUSTOMER_NAME = os.getenv("DEFAULT_CUSTOMER_NAME", "")
DEFAULT_CUSTOMER_PHONE = os.getenv("DEFAULT_CUSTOMER_PHONE", "")
DEFAULT_ORDER_NUM = os.getenv("DEFAULT_ORDER_NUM", "")
REVIEW_TRACE_PATH = str(resolve_project_path(os.getenv("REVIEW_TRACE_PATH", "logs/live_chat_review.jsonl")))
CASE_MEMORY_DIR = str(resolve_project_path(os.getenv("CASE_MEMORY_DIR", "logs/case_memory")))
CASE_TRANSCRIPT_DIR = str(resolve_project_path(os.getenv("CASE_TRANSCRIPT_DIR", "logs/case_transcripts")))
POST_CHAT_AUDIT_DIR = str(resolve_project_path(os.getenv("POST_CHAT_AUDIT_DIR", "logs/post_chat_audits")))
UI_COMMAND_QUEUE_PATH = str(resolve_project_path(os.getenv("UI_COMMAND_QUEUE_PATH", "logs/ui_runtime_commands.jsonl")))
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


def load_run_config():
    raw = (os.getenv(RUN_CONFIG_ENV, "") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def run_cfg_has(config, key):
    return isinstance(config, dict) and key in config


def run_cfg_str(config, key, default=""):
    if not run_cfg_has(config, key):
        return default
    value = config.get(key)
    if value is None:
        return default
    return str(value).strip()


def run_cfg_bool(config, key):
    if not run_cfg_has(config, key):
        return None
    value = config.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off", ""}:
        return False
    return None

SYSTEM_PROMPT = """You are an elite US consumer-rights negotiation copilot writing live support-chat messages as the customer.

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
- Write in first person singular from the account holder's point of view: use "I", "my", and "me".
- Sound like a real customer in a live support chat, not a lawyer, case manager, or outsourced assistant.
- Never describe the case in third person as "the customer", "the buyer", or similar.
- Never use meta phrasing such as "I am assisting with a case", "I am reaching out regarding", or similar.
- Never greet or address the customer by name.
- Never ask the customer to choose a remedy; ask the support agent to confirm the available resolution for me.
- Do not start with filler like "Hello" or "Thank you for your message" unless it directly helps the reply.

QUALITY RULES:
- Write in natural, grammatical US English.
- Fix grammar, spelling, punctuation, capitalization, and phrasing before finalizing the message.
- Avoid broken English, literal translations, slang, filler, repeated words, and robotic wording.
- Use simple business-chat language that sounds like a competent human representative.
- Use only facts provided in the case/chat history. Do not invent facts, policies, or evidence.
- Do not claim to be a lawyer; write as the customer or account holder.
- Answer the operator's latest point in the first sentence.
- If the operator asks for name, email, phone, or order number, provide that exact data in the first sentence before asking for anything else.
- Start cooperative, then become firm if delayed/denied.
- Keep pressure legal and realistic (FCBA/FTC/chargeback) only when needed.
- If agent asks for missing data, provide concise compliance and restate the resolution request.
- Do not repeat the full case history when one or two facts are enough to answer the latest point.
- Avoid "kindly", legal-sounding boilerplate, or stacked demands when one specific ask is enough.
- Use legally grounded pressure when the facts support it, but keep it short and practical.
- Never bluff, invent laws, or state conditional rights as unconditional facts.

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
- The first sentence must directly address the operator's latest claim or request.
- Sound like a capable customer in a real-time chat, not a case manager, legal notice, or template.
- Do not use greetings, gratitude filler, or scene-setting unless it responds to something concrete in the operator's last message.
- Do not use meta phrases like "I am assisting with a case", "I am reaching out regarding", "the customer", "kindly", or "we need".
- If the operator denies receipt despite delivery evidence, say that the tracking shows delivery and ask them to verify the discrepancy.
- If the operator tells the customer to repeat a return that was already delivered, reject that burden shift and ask Lenovo to verify internally instead.
- If the operator asks for contact data or order details, answer with the exact data in sentence one, then ask for the next step.
- If the operator says they are closing the chat or did not hear back, say I am still here and ask for the next concrete action before the chat closes.
- If a case ID exists in the case context, reuse that exact case ID and never invent a different one.
- Use the `legal_context` from the case snapshot when deciding how hard to press.
- Use `resolved_points` and `next_best_asks` from the case snapshot.
- Do not keep asking for a case ID, escalation owner, or policy text after the operator already provided it, unless you are pointing out that it was incomplete or contradictory.
- When the operator gives a partial answer, narrow the next ask to the single missing point instead of repeating the full escalation bundle.
- For an online order or replacement that remains unshipped after delay, you may say I am not agreeing to an open-ended delay and ask for cancellation plus a prompt refund.
- Only mention FCBA, billing-dispute rights, Regulation Z, or a card-issuer dispute conditionally unless the case context confirms a credit-card purchase.
- If you mention a legal or regulatory basis, keep it to one short clause and tie it directly to the facts.
- Prefer written basis, escalation owner, case ID, deadline, prompt refund, and billing-dispute preservation over long legal lectures.
- If the chat is not truly ready for a live agent message, choose wait.
- If the agent asks for data already known in the case, answer directly and concisely.
- If the agent is vague or stalling, ask for a concrete action, case ID, escalation owner, or timeline.
- Write in natural, grammatical US English.
- Write to the support agent, not to the customer.
- Write strictly in first person singular as the account holder: use "I", "my", and "me".
- Never describe yourself as "the customer", "the buyer", or "the account holder" in third person.
- Never address the customer by name.
- Never ask what the customer prefers; ask the support agent to confirm what they can do for me.
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

REPLY_CRITIC_PROMPT = """You are a reply critic for a live support-chat agent.

Return JSON only.

Goal:
- Check whether the drafted reply logically addresses the latest operator message.
- Reject replies that ignore the operator's latest point, repeat the prior demand without progress, or weaken the case.
- Reject replies whose first sentence does not directly answer the operator's latest claim.
- Reject replies that accept an unreasonable burden shift back to the customer when the merchant should verify its own warehouse/return records.
- Reject replies written in third person about the customer. The reply must be in first person singular.
- Reject replies with filler openings like generic greetings or "Thank you for your message" when they delay the actual answer.
- Reject meta phrasing such as "I am assisting with a case", "the customer", "we need", or an invented/wrong case ID.
- Reject replies that sound like a scripted legal notice instead of a live customer message.
- Reject replies that restate the whole case when a narrow answer would be stronger.
- Reject replies that overclaim legal rights, cite the wrong legal basis for the facts, or present conditional card-dispute rights as unconditional.
- Reject replies that threaten regulators, lawsuits, or criminal consequences when the case snapshot does not support that escalation.
- Reject replies that keep asking for already-resolved points when the case snapshot shows a narrower missing point to pursue instead.

JSON schema:
{
  "approved": true,
  "reason": "string",
  "fix": "string"
}

Rules:
- `approved` must be false if the draft does not answer the latest operator move.
- `fix` must be empty when approved is true.
- `fix` must be a short improved replacement when approved is false.
- Never write from the merchant's point of view.
"""

POST_CHAT_AUDIT_PROMPT = """You are reviewing a completed support-chat transcript where the customer-side messages were written by a bot.

Return JSON only.

Goal:
- Judge whether the bot sounded like a real human customer or like a reusable template.
- Focus on the customer-side messages only.
- Use the transcript, case snapshot, and heuristic stats together.
- Be strict about repetitive phrasing, robotic escalation ladders, and fake-lawyer tone.
- Separate strong legal pressure from bad scripted pressure.

JSON schema:
{
  "summary": "string",
  "verdict": "human_like|mixed|templated",
  "human_likeness_score": 0.0,
  "template_risk_score": 0.0,
  "persuasion_score": 0.0,
  "legal_grounding_score": 0.0,
  "strengths": ["string"],
  "bot_signals": ["string"],
  "notable_examples": [
    {
      "quote": "string",
      "why": "string"
    }
  ],
  "recommended_fixes": ["string"]
}

Rules:
- All four scores must be on a 0-10 scale.
- Quote only short excerpts.
- Call out specific repeated openers, repeated asks, or sentence shapes when they hurt realism.
- Reward direct, context-aware answers that feel tailored to the operator's last point.
- Penalize generic escalation patterns that would be obvious to a human operator.
- Penalize legal overclaiming, but reward concise fact-based legal leverage.
- Recommended fixes must be implementation-focused, not generic writing advice.
"""

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
        self.dialogue_state = "opening"
        self.unresolved_demands = []
        self.confirmed_facts = []
        self.operator_claims = []
        self.contradictions = []
        self.lenovo_widget_reset_done = False
        self.review_trace_path = REVIEW_TRACE_PATH
        self.case_memory_dir = CASE_MEMORY_DIR
        self.case_memory_path = self._build_case_memory_path()
        self.case_transcript_dir = CASE_TRANSCRIPT_DIR
        self.case_transcript_path = self._build_case_transcript_path()
        self.post_chat_audit_dir = POST_CHAT_AUDIT_DIR
        self.case_audit_path = self._build_case_audit_path()
        self.ui_session_id = (os.getenv(UI_SESSION_ENV, "") or "").strip()
        self.ui_command_queue_path = str(resolve_project_path(os.getenv(UI_COMMAND_QUEUE_ENV, UI_COMMAND_QUEUE_PATH)))
        self.ui_command_offset = 0
        self.last_critic_verdict = {}
        self.latest_case_id = ""
        self.latest_case_outcome = ""
        self.follow_up_deadline = ""
        self.follow_up_anchor_at = ""
        self.last_event_at = ""
        self.last_saved_at = ""
        self.operator_notes = []
        self.pending_requested_field = ""
        self._load_case_memory()

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

    def _strip_customer_echo(self, text):
        t = (text or "").strip()
        if not t:
            return ""
        if t.lower().startswith("your message"):
            remainder = re.sub(r"^your message[\s:,-]*", "", t, flags=re.I).strip()
            return remainder or t
        return t

    def _operator_text(self, text):
        t = (text or "").strip()
        if not t:
            return ""
        t = re.sub(r"\b(?:lenovo|advisor message)\b", " ", t, flags=re.I)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _case_id_pattern(self):
        return r"\bC[A-Z]?\d{6,12}\b"

    def _contains_case_id(self, text):
        return bool(re.search(self._case_id_pattern(), text or "", flags=re.I))

    def _extract_case_ids(self, text):
        return [item.upper() for item in re.findall(self._case_id_pattern(), text or "", flags=re.I)]

    def _explicit_field_request(self, text, field_name):
        t = self._operator_text(text).lower()
        if not t:
            return False
        blocked_fragments = {
            "email": [
                "unable to send emails directly",
                "sent an email",
                "emailed the team",
                "reach out to the relevant team via email",
                "provide you with a transcript",
                "chat transcript",
                "order email is",
                "your order email",
                "email the team",
            ],
            "phone": [
                "phone support",
                "call us",
                "contact phone",
            ],
            "order": [
                "order status",
                "existing orders",
            ],
            "name": [],
        }
        if any(fragment in t for fragment in blocked_fragments.get(field_name, [])):
            return False
        patterns = {
            "email": [
                r"\b(?:please|kindly|can you|could you|would you|may i|i need you to)\b[^.?!]{0,40}\b(?:provide|confirm|share|enter|verify|give)\b[^.?!]{0,40}\b(?:email|e-mail|email address)\b",
                r"\bwhat(?:'s| is)\s+your\s+(?:email|e-mail|email address)\b",
                r"\b(?:email|e-mail|email address)\b[^.?!]{0,20}\?$",
                r"\bemail on the order\b[^.?!]{0,25}\b(?:please|can you|could you|share|confirm|provide)\b",
            ],
            "phone": [
                r"\b(?:please|kindly|can you|could you|would you|may i|i need you to)\b[^.?!]{0,40}\b(?:provide|confirm|share|enter|verify|give)\b[^.?!]{0,40}\b(?:phone|phone number|contact number)\b",
                r"\bwhat(?:'s| is)\s+your\s+(?:phone|phone number|contact number)\b",
                r"\b(?:phone|phone number|contact number)\b[^.?!]{0,20}\?$",
            ],
            "order": [
                r"\b(?:please|kindly|can you|could you|would you|may i|i need you to)\b[^.?!]{0,40}\b(?:provide|confirm|share|enter|verify|give)\b[^.?!]{0,40}\border(?: number| no\.?| #)?\b",
                r"\bwhat(?:'s| is)\s+your\s+order(?: number| no\.?| #)?\b",
                r"\border(?: number| no\.?| #)?\b[^.?!]{0,20}\?$",
            ],
            "name": [
                r"\b(?:please|kindly|can you|could you|would you|may i|i need you to)\b[^.?!]{0,40}\b(?:provide|confirm|share|enter|verify|give)\b[^.?!]{0,40}\bname\b",
                r"\bwhat(?:'s| is)\s+your\s+name\b",
                r"\bname on the order\b",
            ],
        }
        return any(re.search(pattern, t, flags=re.I) for pattern in patterns.get(field_name, []))

    def _known_case_points(self):
        claims_text = " ".join(self.operator_claims).lower()
        last_agent = (self.last_agent_msg or "").lower()
        text = f"{claims_text} {last_agent}".strip()
        return {
            "case_id_confirmed": bool(self.latest_case_id) or self._contains_case_id(text),
            "escalation_owner_known": any(x in text for x in ["case manager", "escalation owner", "na csat"]),
            "policy_text_provided": any(x in text for x in ["written policy:", "exact policy:", "refunds are processed after concession and case review is complete"]),
            "no_policy_basis_admitted": "there is no return policy that justifies withholding your refund" in text,
            "approval_dependency_stated": any(x in text for x in ["once approved", "after approval", "under review", "concession request", "case review is complete"]),
            "no_firm_deadline": any(x in text for x in ["do not have a firm deadline", "no firm deadline", "firm refund date depends"]),
            "callback_window_given": any(x in text for x in ["24-48 business hours", "24 to 48 business hours", "reach out to us again", "contact us again"]),
            "returns_team_confirmed": "returns team" in text,
            "supervisor_same_resolution": "same resolution" in text,
            "internal_email_update_only": any(x in text for x in ["emailed the team", "sent an email to our team", "reach out to the relevant team via email"]),
        }

    def resolved_points(self):
        points = self._known_case_points()
        resolved = []
        if points["case_id_confirmed"]:
            resolved.append("case ID already confirmed")
        if points["escalation_owner_known"]:
            resolved.append("escalation owner already identified")
        if points["policy_text_provided"]:
            resolved.append("operator already provided policy wording")
        if points["no_policy_basis_admitted"]:
            resolved.append("operator admitted there is no separate return policy basis for withholding the refund")
        if points["approval_dependency_stated"]:
            resolved.append("operator said the refund depends on concession or approval review")
        if points["callback_window_given"]:
            resolved.append("operator asked for a 24-48 business hour follow-up window")
        return resolved[:6]

    def next_best_asks(self, agent_text=""):
        intent = self.infer_agent_intent(agent_text or self.last_agent_msg)
        points = self._known_case_points()
        asks = []
        if intent == "consumer_type_question":
            return asks
        if intent in {"hold_request", "keepalive"}:
            asks.append("tell me the exact next step and timeline")
            return asks
        if intent == "transcript_offer":
            asks.append("confirm the escalation owner and next update deadline, not only the transcript")
            return asks
        if not points["case_id_confirmed"]:
            asks.append("confirm the case ID")
        if not points["escalation_owner_known"]:
            asks.append("name the escalation owner or team that owns the review")
        if not points["policy_text_provided"] and not points["no_policy_basis_admitted"]:
            asks.append("quote the exact policy or written basis for why the refund is still pending")
        if points["no_policy_basis_admitted"]:
            asks.append("state what exact approval is still pending")
            asks.append("give the date or deadline for when that approval review will finish")
        elif points["policy_text_provided"] or points["approval_dependency_stated"]:
            asks.append("state what exact approval or concession step is still pending")
            asks.append("give the deadline for when that approval review will finish")
        elif points["callback_window_given"]:
            asks.append("confirm who owns the case during the 24-48 business hour follow-up window")
            asks.append("confirm the next written update deadline")
        elif not points["approval_dependency_stated"]:
            asks.append("explain exactly what is still blocking the refund")
        if points["supervisor_same_resolution"]:
            asks.append("tell me who has authority to approve the refund if the supervisor cannot change it")
        if points["internal_email_update_only"]:
            asks.append("tell me what exactly was requested in the internal escalation and when the next update is due")
        deduped = []
        for ask in asks:
            if ask not in deduped:
                deduped.append(ask)
        return deduped[:3]

    def legal_pressure_level(self):
        intent = self.infer_agent_intent(self.last_agent_msg)
        if self.message_count >= 3 or intent in {
            "return_required_claim",
            "customer_retrieve_and_rereturn",
            "ups_redirect",
            "empty_box_claim",
            "warehouse_missing_claim",
            "closure_warning",
        }:
            return "high"
        if intent in {"case_id_provided", "escalation_confirmed", "timeline_statement"} or self.message_count >= 2:
            return "medium"
        return "low"

    # Ground legal pressure in official FTC/CFPB consumer-rights concepts without overclaiming applicability.
    def legal_context(self):
        case = (self.case_type or "").upper()
        pressure = self.legal_pressure_level()
        allowed_anchors = [
            "Ask for the written policy basis, escalation owner, case ID, and a concrete deadline.",
            "Keep any legal reference short and tied to the facts already in the case snapshot.",
        ]
        if case == "INR":
            allowed_anchors.extend([
                "For an online order that was not delivered on time, I can refuse an open-ended delay and ask for cancellation plus a prompt refund.",
                "If this charge was on a credit card, I may preserve billing-dispute rights with the card issuer if the merchant does not resolve the non-delivery.",
            ])
        if case in {"RNR", "REFUND", "DOA", "DAMAGED", "DEFECTIVE", "BROKEN"}:
            allowed_anchors.extend([
                "If Lenovo already has the return or cannot fulfill the replacement, I can demand the written basis for withholding the refund and a concrete refund timeline.",
                "If the replacement remains unfulfilled, I do not have to accept an open-ended replacement delay and can ask Lenovo to cancel that path and confirm the refund.",
            ])
        if pressure == "high":
            allowed_anchors.append(
                "If the operator keeps stalling or denying without basis, I can say that if this purchase was on a credit card I will preserve my billing-dispute rights and need Lenovo's written basis today."
            )
        return {
            "pressure_level": pressure,
            "allowed_anchors": allowed_anchors[:4],
            "forbidden_anchors": [
                "Do not claim to be a lawyer or threaten criminal penalties.",
                "Do not present card-dispute rights as unconditional unless a credit-card purchase is known.",
                "Do not cite legal rights that do not fit the facts in the current case.",
                "Do not threaten regulators or lawsuits unless the dialogue has clearly reached that stage.",
            ],
            "preferred_asks": [
                "written policy basis",
                "supervisor or returns-team escalation",
                "case ID",
                "exact refund or follow-up timeline",
            ],
        }

    def active_case_reference(self):
        return f"case ID {self.latest_case_id}" if self.latest_case_id else "this case"

    def _requested_customer_field(self, text):
        if self._explicit_field_request(text, "email"):
            return "email"
        if self._explicit_field_request(text, "phone"):
            return "phone"
        if self._explicit_field_request(text, "order"):
            return "order"
        if self._explicit_field_request(text, "name"):
            return "name"
        return ""

    def generate_first_message(self):
        plan = self.plan_next_action(
            agent_text="",
            observation={"chat_ready": True, "first_turn": True},
            first_turn=True,
        )
        reply = self._polish_chat_reply(plan.get("message") or "", first_turn=True)
        if not reply:
            prompt = f"""Start a live support chat with {self.store}.
Case: {self.case_name()}
Order: {self.order_num or "N/A"}
Amount: {f"${self.amount}" if self.amount else "N/A"}
Details: {self.details or "none"}
Write the first message to the support agent.
Make it polished, grammatical, and natural."""

            self.history = [{"role": "user", "content": prompt}]
            reply = self._polish_chat_reply(self._call_llm(), first_turn=True)
        self.history.append({"role": "assistant", "content": reply})
        self._append_transcript_entry("customer_rep", reply)
        self.message_count = 1
        self.last_sent_msg = reply
        return reply

    def generate_reply(self, agent_text, already_recorded=False):
        if not already_recorded:
            self.record_agent_message(agent_text)
        plan = self.plan_next_action(
            agent_text=agent_text,
            observation={"chat_ready": True, "message_count": self.message_count},
            first_turn=False,
        )
        reply = self._polish_chat_reply(plan.get("message") or "", agent_text=agent_text, first_turn=False)
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
            reply = self._polish_chat_reply(self._call_llm(), agent_text=agent_text, first_turn=False)
        self.history.append({"role": "assistant", "content": reply})
        self._append_transcript_entry("customer_rep", reply)
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
            "legal_context": self.legal_context(),
            "resolved_points": self.resolved_points(),
            "next_best_asks": self.next_best_asks(),
            "dialogue_state": self.dialogue_state,
            "unresolved_demands": self.unresolved_demands[-6:],
            "confirmed_facts": self.confirmed_facts[-8:],
            "operator_claims": self.operator_claims[-8:],
            "contradictions": self.contradictions[-6:],
            "latest_case_id": self.latest_case_id,
            "latest_case_outcome": self.latest_case_outcome,
            "follow_up_deadline": self.follow_up_deadline,
            "operator_notes": self.operator_notes[-6:],
            "pending_requested_field": self.pending_requested_field,
            "transcript_message_count": len(self.transcript),
            "conversation_outline": self._conversation_outline(),
        }

    def _case_memory_key(self):
        store_key = re.sub(r"[^a-z0-9]+", "-", (self.store or "unknown").lower()).strip("-") or "unknown"
        order_key = re.sub(r"[^a-z0-9]+", "", (self.order_num or "").lower()) or "noorder"
        return f"{store_key}_{order_key}"

    def _build_case_memory_path(self):
        return str(Path(self.case_memory_dir) / f"{self._case_memory_key()}.json")

    def _build_case_transcript_path(self):
        return str(Path(self.case_transcript_dir) / f"{self._case_memory_key()}.json")

    def _build_case_audit_path(self):
        return str(Path(self.post_chat_audit_dir) / f"{self._case_memory_key()}.md")

    def _refresh_case_storage_paths(self, move_existing=False):
        old_memory_path = Path(self.case_memory_path) if self.case_memory_path else None
        old_transcript_path = Path(self.case_transcript_path) if self.case_transcript_path else None
        old_audit_path = Path(self.case_audit_path) if self.case_audit_path else None
        new_memory_path = Path(self._build_case_memory_path())
        new_transcript_path = Path(self._build_case_transcript_path())
        new_audit_path = Path(self._build_case_audit_path())
        if move_existing:
            try:
                if old_memory_path and old_memory_path != new_memory_path and old_memory_path.exists() and not new_memory_path.exists():
                    new_memory_path.parent.mkdir(parents=True, exist_ok=True)
                    old_memory_path.rename(new_memory_path)
            except Exception:
                pass
            try:
                if old_transcript_path and old_transcript_path != new_transcript_path and old_transcript_path.exists() and not new_transcript_path.exists():
                    new_transcript_path.parent.mkdir(parents=True, exist_ok=True)
                    old_transcript_path.rename(new_transcript_path)
            except Exception:
                pass
            try:
                if old_audit_path and old_audit_path != new_audit_path and old_audit_path.exists() and not new_audit_path.exists():
                    new_audit_path.parent.mkdir(parents=True, exist_ok=True)
                    old_audit_path.rename(new_audit_path)
            except Exception:
                pass
        self.case_memory_path = str(new_memory_path)
        self.case_transcript_path = str(new_transcript_path)
        self.case_audit_path = str(new_audit_path)

    def _utc_now_iso(self):
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    def _normalize_transcript_entry(self, entry):
        if isinstance(entry, dict):
            role = (entry.get("role") or "").strip()
            content = (entry.get("content") or "").strip()
            ts = (entry.get("ts") or "").strip()
        else:
            role = ""
            content = ""
            ts = ""
        if role == "customer_rep":
            content = self._strip_customer_echo(content)
        if not role or not content:
            return None
        normalized = {"role": role, "content": content}
        if ts:
            normalized["ts"] = ts
        return normalized

    def _normalize_transcript_entries(self, entries):
        normalized = []
        for entry in entries or []:
            item = self._normalize_transcript_entry(entry)
            if item:
                normalized.append(item)
        return normalized

    def _append_transcript_entry(self, role, content):
        role = (role or "").strip()
        content = (content or "").strip()
        if role == "customer_rep":
            content = self._strip_customer_echo(content)
        if not role or not content:
            return
        if self.transcript:
            last = self._normalize_transcript_entry(self.transcript[-1])
            if last and last.get("role") == role and self._normalize_message(last.get("content")) == self._normalize_message(content):
                return
        entry = {
            "role": role,
            "content": content,
            "ts": self._utc_now_iso(),
        }
        self.transcript.append(entry)
        self.last_event_at = entry["ts"]

    def _rebuild_transcript_from_review_trace(self):
        try:
            trace_path = Path(self.review_trace_path)
            if not trace_path.exists():
                return []
            transcript = []
            target_case_type = self.case_name()
            target_order = (self.order_num or "").strip()
            with trace_path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if target_order and (record.get("order_num") or "").strip() != target_order:
                        continue
                    if (record.get("case_type") or "").strip() != target_case_type:
                        continue
                    event = (record.get("event") or "").strip()
                    payload = record.get("payload") or {}
                    message = (payload.get("message") or "").strip()
                    if not message:
                        continue
                    if event == "agent_message":
                        role = "agent"
                    elif event in {"sent_message", "customer_message_sync"}:
                        role = "customer_rep"
                    else:
                        continue
                    item = {
                        "role": role,
                        "content": message,
                    }
                    if record.get("ts"):
                        item["ts"] = record["ts"]
                    if transcript:
                        last = transcript[-1]
                        if last.get("role") == role and self._normalize_message(last.get("content")) == self._normalize_message(message):
                            continue
                    transcript.append(item)
            return transcript
        except Exception:
            return []

    def _load_full_transcript(self):
        try:
            path = Path(self.case_transcript_path)
            if not path.exists():
                rebuilt = self._rebuild_transcript_from_review_trace()
                if rebuilt:
                    self.transcript = self._normalize_transcript_entries(rebuilt)
                    self._persist_full_transcript()
                else:
                    self.transcript = self._normalize_transcript_entries(self.transcript)
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            full_transcript = self._normalize_transcript_entries(data)
            if full_transcript:
                self.transcript = full_transcript
            else:
                self.transcript = self._normalize_transcript_entries(self.transcript)
        except Exception:
            self.transcript = self._normalize_transcript_entries(self.transcript)

    def _persist_full_transcript(self):
        try:
            path = Path(self.case_transcript_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._normalize_transcript_entries(self.transcript)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _sync_message_count_from_transcript(self):
        customer_messages = 0
        last_customer = ""
        for item in self.transcript:
            if item.get("role") != "customer_rep":
                continue
            content = self._strip_customer_echo(item.get("content") or "")
            normalized = self._normalize_message(content)
            if normalized and normalized != last_customer:
                customer_messages += 1
                last_customer = normalized
        self.message_count = customer_messages

    def _sync_last_event_from_transcript(self):
        if not self.transcript:
            return
        for item in reversed(self.transcript):
            ts = (item.get("ts") or "").strip()
            if ts:
                self.last_event_at = ts
                return

    def _transcript_follow_up_anchor(self):
        if not self.follow_up_deadline:
            return ""
        deadline_text = (self.follow_up_deadline or "").lower().strip()
        for item in reversed(self.transcript):
            if item.get("role") != "agent":
                continue
            content = (item.get("content") or "").lower()
            ts = (item.get("ts") or "").strip()
            if not content or not ts:
                continue
            if deadline_text and deadline_text in content:
                return ts
            if "48 hours" in deadline_text and "48 hours" in content:
                return ts
            if "business days" in deadline_text and "business days" in content:
                return ts
        return ""

    def _sync_follow_up_anchor_from_transcript(self):
        if not self.follow_up_deadline:
            return
        transcript_anchor = self._transcript_follow_up_anchor()
        if transcript_anchor:
            self.follow_up_anchor_at = transcript_anchor

    def _field_value(self, field_name):
        mapping = {
            "email": self.customer_email,
            "phone": self.customer_phone,
            "order": self.order_num,
            "name": self.customer_name,
        }
        return (mapping.get(field_name, "") or "").strip()

    def _has_requested_field(self, field_name):
        return bool(self._field_value(field_name))

    def _requested_field_label(self, field_name):
        labels = {
            "email": "email",
            "phone": "phone",
            "order": "order number",
            "name": "name",
        }
        return labels.get(field_name, field_name or "data")

    def _apply_runtime_hint(self, text):
        note = (text or "").strip()
        if not note:
            return False
        self._append_unique(self.operator_notes, note, limit=12)
        print(f"💡 Подсказка из UI: {note}")
        self._append_review_trace("ui_hint", {"message": note})
        self._persist_case_memory()
        return True

    def _apply_runtime_case_data(self, payload):
        payload = payload or {}
        changed = []

        name = normalize_customer_name(payload.get("customer_name") or payload.get("name") or self.customer_name)
        email = normalize_customer_email(payload.get("customer_email") or payload.get("email") or self.customer_email)
        phone = normalize_customer_phone(payload.get("customer_phone") or payload.get("phone") or self.customer_phone)
        order = normalize_order_num(payload.get("order_num") or payload.get("order") or self.order_num)
        details = (payload.get("details") or "").strip()

        if name and name != self.customer_name:
            self.customer_name = name
            changed.append(f"name={self.customer_name}")
        if email and email != self.customer_email:
            self.customer_email = email
            changed.append(f"email={self.customer_email}")
        if phone and phone != self.customer_phone:
            self.customer_phone = phone
            changed.append(f"phone={self.customer_phone}")
        if order and order != self.order_num:
            self.order_num = order
            self._refresh_case_storage_paths(move_existing=True)
            changed.append(f"order={self.order_num}")
        if details:
            merged_details = (self.details or "").strip()
            if details not in merged_details:
                self.details = "\n".join(part for part in [merged_details, details] if part).strip()
                changed.append("details=updated")

        if not changed:
            return False

        print(f"🗂 Обновлены данные кейса из UI: {', '.join(changed)}")
        self._append_review_trace("ui_case_data", {"changes": changed})
        self._persist_case_memory()
        return True

    def consume_ui_commands(self):
        queue_path = Path(self.ui_command_queue_path)
        if not self.ui_session_id or not queue_path.exists():
            return []
        applied = []
        try:
            size = queue_path.stat().st_size
            if self.ui_command_offset > size:
                self.ui_command_offset = 0
            with queue_path.open("r", encoding="utf-8") as fh:
                fh.seek(self.ui_command_offset)
                while True:
                    raw_line = fh.readline()
                    if not raw_line:
                        break
                    self.ui_command_offset = fh.tell()
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        command = json.loads(line)
                    except Exception:
                        continue
                    if (command.get("session_id") or "").strip() != self.ui_session_id:
                        continue
                    command_type = (command.get("type") or "").strip()
                    payload = command.get("payload") or {}
                    if command_type == "hint":
                        if self._apply_runtime_hint(payload.get("text") or ""):
                            applied.append(command_type)
                    elif command_type == "case_data":
                        if self._apply_runtime_case_data(payload):
                            applied.append(command_type)
        except Exception:
            return applied
        return applied

    def _planning_transcript_window(self, head=4, tail=14):
        transcript = []
        for item in self._normalize_transcript_entries(self.transcript):
            role = item.get("role")
            content = item.get("content", "")
            if role == "customer_rep":
                content = self._strip_customer_echo(content)
            if not content:
                continue
            normalized = {"role": role, "content": content}
            if item.get("ts"):
                normalized["ts"] = item["ts"]
            if transcript:
                last = transcript[-1]
                if last.get("role") == role and self._normalize_message(last.get("content")) == self._normalize_message(content):
                    continue
            transcript.append(normalized)
        if len(transcript) <= head + tail:
            return transcript
        hidden = len(transcript) - head - tail
        return transcript[:head] + [{
            "role": "context",
            "content": f"... {hidden} earlier messages omitted from the prompt window, but they remain part of the saved case transcript.",
        }] + transcript[-tail:]

    def _conversation_outline(self):
        outline = []
        first_customer = next((item.get("content", "") for item in self.transcript if item.get("role") == "customer_rep"), "")
        if first_customer:
            outline.append(f"Case opened with: {first_customer}")
        if self.latest_case_id:
            outline.append(f"Active case ID: {self.latest_case_id}")
        if self.latest_case_outcome:
            outline.append(f"Current merchant position: {self.latest_case_outcome}")
        elif self.last_agent_msg:
            outline.append(f"Latest merchant message: {self.last_agent_msg}")
        if self.follow_up_deadline:
            outline.append(f"Current wait window: {self.follow_up_deadline}")
        return outline[:4]

    def _audit_transcript_window(self, head=8, tail=40):
        transcript = []
        for item in self._normalize_transcript_entries(self.transcript):
            role = item.get("role")
            content = item.get("content", "")
            if role == "customer_rep":
                content = self._strip_customer_echo(content)
            if not content:
                continue
            normalized = {"role": role, "content": content}
            if item.get("ts"):
                normalized["ts"] = item["ts"]
            if transcript:
                last = transcript[-1]
                if last.get("role") == role and self._normalize_message(last.get("content")) == self._normalize_message(content):
                    continue
            transcript.append(normalized)
        if len(transcript) <= head + tail:
            return transcript
        hidden = len(transcript) - head - tail
        return transcript[:head] + [{
            "role": "context",
            "content": f"... {hidden} earlier messages omitted from the audit prompt, but they remain part of the saved transcript.",
        }] + transcript[-tail:]

    def _post_chat_audit_stats(self):
        transcript = self._normalize_transcript_entries(self.transcript)
        customer_messages = []
        for item in transcript:
            if item.get("role") != "customer_rep":
                continue
            content = self._strip_customer_echo(item.get("content", ""))
            if customer_messages and self._normalize_message(customer_messages[-1]) == self._normalize_message(content):
                continue
            if content:
                customer_messages.append(content)
        agent_messages = [item.get("content", "") for item in transcript if item.get("role") == "agent"]
        opener_counter = Counter()
        marker_counter = Counter()
        near_duplicate_count = 0
        template_markers = [
            "please confirm",
            "written basis",
            "case id",
            "timeline",
            "escalate",
            "refund status",
            "i need",
            "tracking already shows",
        ]
        normalized_messages = []
        for msg in customer_messages:
            lowered = self._normalize_message(msg)
            normalized_messages.append(lowered)
            words = re.findall(r"[a-z0-9']+", lowered)
            opener = " ".join(words[:3]) if words else lowered[:24]
            if opener:
                opener_counter[opener] += 1
            for marker in template_markers:
                if marker in lowered:
                    marker_counter[marker] += 1
        for idx, current in enumerate(normalized_messages):
            for prev in normalized_messages[:idx]:
                if difflib.SequenceMatcher(a=current, b=prev).ratio() >= 0.88:
                    near_duplicate_count += 1
                    break
        avg_words = 0.0
        if customer_messages:
            avg_words = round(sum(len(msg.split()) for msg in customer_messages) / len(customer_messages), 1)
        return {
            "customer_message_count": len(customer_messages),
            "agent_message_count": len(agent_messages),
            "avg_customer_words": avg_words,
            "near_duplicate_count": near_duplicate_count,
            "top_openers": [{"opener": opener, "count": count} for opener, count in opener_counter.most_common(5)],
            "template_markers": [{"phrase": phrase, "count": count} for phrase, count in marker_counter.items() if count > 0],
        }

    def _render_post_chat_audit(self, audit, reason, stats):
        summary = (audit.get("summary") or "Audit completed.").strip()
        verdict = (audit.get("verdict") or "mixed").strip()
        human_score = float(audit.get("human_likeness_score") or 0.0)
        template_score = float(audit.get("template_risk_score") or 0.0)
        persuasion_score = float(audit.get("persuasion_score") or 0.0)
        legal_score = float(audit.get("legal_grounding_score") or 0.0)
        strengths = [str(item).strip() for item in (audit.get("strengths") or []) if str(item).strip()]
        bot_signals = [str(item).strip() for item in (audit.get("bot_signals") or []) if str(item).strip()]
        fixes = [str(item).strip() for item in (audit.get("recommended_fixes") or []) if str(item).strip()]
        examples = audit.get("notable_examples") or []
        lines = [
            "# Post-Chat Audit",
            "",
            f"- Generated: {self._utc_now_iso()}",
            f"- Reason: {reason}",
            f"- Store: {self.store}",
            f"- Case: {self.case_name()}",
            f"- Order: {self.order_num or 'N/A'}",
            f"- Verdict: {verdict}",
            f"- Human-likeness: {human_score:.1f}/10",
            f"- Template risk: {template_score:.1f}/10",
            f"- Persuasion: {persuasion_score:.1f}/10",
            f"- Legal grounding: {legal_score:.1f}/10",
            "",
            "## Summary",
            summary,
            "",
            "## Heuristics",
            f"- Customer messages: {stats.get('customer_message_count', 0)}",
            f"- Agent messages: {stats.get('agent_message_count', 0)}",
            f"- Avg customer words/message: {stats.get('avg_customer_words', 0.0)}",
            f"- Near-duplicate customer replies: {stats.get('near_duplicate_count', 0)}",
        ]
        top_openers = stats.get("top_openers") or []
        if top_openers:
            opener_text = ", ".join(f"`{item['opener']}` x{item['count']}" for item in top_openers)
            lines.append(f"- Repeated openers: {opener_text}")
        markers = stats.get("template_markers") or []
        if markers:
            marker_text = ", ".join(f"`{item['phrase']}` x{item['count']}" for item in markers)
            lines.append(f"- Template markers: {marker_text}")
        if strengths:
            lines.extend(["", "## Human Signals"] + [f"- {item}" for item in strengths])
        if bot_signals:
            lines.extend(["", "## Bot Signals"] + [f"- {item}" for item in bot_signals])
        if examples:
            lines.append("")
            lines.append("## Notable Examples")
            for item in examples[:5]:
                quote = str((item or {}).get("quote") or "").strip()
                why = str((item or {}).get("why") or "").strip()
                if quote:
                    lines.append(f"- `{quote}`")
                    if why:
                        lines.append(f"  {why}")
        if fixes:
            lines.extend(["", "## Recommended Fixes"] + [f"- {item}" for item in fixes])
        return "\n".join(lines).strip() + "\n"

    def generate_post_chat_audit(self, reason="session_end"):
        transcript = self._normalize_transcript_entries(self.transcript)
        if not transcript:
            return ""
        stats = self._post_chat_audit_stats()
        audit_input = {
            "reason": reason,
            "case": self.build_case_snapshot(),
            "heuristics": stats,
            "transcript": self._audit_transcript_window(),
        }
        audit = {}
        error = ""
        try:
            raw = self._call_llm(
                system_prompt=POST_CHAT_AUDIT_PROMPT,
                history=[{"role": "user", "content": json.dumps(audit_input, ensure_ascii=True)}],
                temperature=0.0,
                sanitize=False,
                max_tokens=1400,
            )
            audit = self._extract_json_object(raw) or {}
        except Exception as e:
            error = str(e)
        if isinstance(audit, dict):
            score_keys = [
                "human_likeness_score",
                "template_risk_score",
                "persuasion_score",
                "legal_grounding_score",
            ]
            raw_scores = [float(audit.get(key) or 0.0) for key in score_keys]
            if raw_scores and any(score > 0 for score in raw_scores) and all(0.0 <= score <= 1.0 for score in raw_scores):
                for key, score in zip(score_keys, raw_scores):
                    audit[key] = round(score * 10.0, 1)
        if not isinstance(audit, dict) or not audit:
            repeated_openers = stats.get("top_openers") or []
            opener_note = repeated_openers[0]["opener"] if repeated_openers else "n/a"
            template_risk = 7.0 if stats.get("near_duplicate_count", 0) >= 2 else 5.0
            human_likeness = 4.0 if template_risk >= 7.0 else 6.0
            audit = {
                "summary": "LLM audit was unavailable, so this report was generated from local transcript heuristics.",
                "verdict": "templated" if template_risk >= 7.0 else "mixed",
                "human_likeness_score": human_likeness,
                "template_risk_score": template_risk,
                "persuasion_score": 6.0,
                "legal_grounding_score": 6.0,
                "strengths": [
                    "The audit still has a full saved transcript and message-level heuristics.",
                ],
                "bot_signals": [
                    f"Most repeated opener: {opener_note}",
                    f"Near-duplicate customer replies detected: {stats.get('near_duplicate_count', 0)}",
                ],
                "notable_examples": [],
                "recommended_fixes": [
                    "Reduce repeated opener patterns across customer replies.",
                    "Vary the final ask so multiple turns do not end with the same structure.",
                ],
            }
            if error:
                audit["bot_signals"].append(f"Audit fallback reason: {error}")
        report = self._render_post_chat_audit(audit, reason, stats)
        try:
            path = Path(self.case_audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report, encoding="utf-8")
            self._append_review_trace(
                "post_chat_audit",
                {
                    "reason": reason,
                    "audit_path": str(path),
                    "verdict": audit.get("verdict") or "",
                    "human_likeness_score": float(audit.get("human_likeness_score") or 0.0),
                    "template_risk_score": float(audit.get("template_risk_score") or 0.0),
                },
            )
            return str(path)
        except Exception:
            return ""

    def _load_case_memory(self):
        try:
            path = Path(self.case_memory_path)
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self.last_agent_msg = data.get("last_agent_message", self.last_agent_msg)
                self.last_sent_msg = self._strip_customer_echo(data.get("last_customer_message", self.last_sent_msg))
                self.dialogue_state = data.get("dialogue_state", self.dialogue_state)
                self.unresolved_demands = list(data.get("unresolved_demands", self.unresolved_demands))[-12:]
                self.confirmed_facts = list(data.get("confirmed_facts", self.confirmed_facts))[-12:]
                self.operator_claims = list(data.get("operator_claims", self.operator_claims))[-12:]
                self.contradictions = list(data.get("contradictions", self.contradictions))[-12:]
                self.latest_case_id = data.get("latest_case_id", self.latest_case_id)
                self.latest_case_outcome = data.get("latest_case_outcome", self.latest_case_outcome)
                self.follow_up_deadline = data.get("follow_up_deadline", self.follow_up_deadline)
                self.follow_up_anchor_at = data.get("follow_up_anchor_at", self.follow_up_anchor_at)
                self.last_event_at = data.get("last_event_at", self.last_event_at)
                self.operator_notes = list(data.get("operator_notes", self.operator_notes))[-12:]
                self.pending_requested_field = data.get("pending_requested_field", self.pending_requested_field)
                self.message_count = int(data.get("message_count", self.message_count) or 0)
                self.transcript = list(data.get("transcript_tail", self.transcript))[-24:]
                self.last_saved_at = data.get("updated_at", self.last_saved_at)
        except Exception:
            pass
        self._load_full_transcript()
        self._rebuild_case_state_from_transcript()
        self._sync_message_count_from_transcript()
        self._sync_last_event_from_transcript()
        self._sync_follow_up_anchor_from_transcript()

    def _reset_derived_case_state(self):
        self.message_count = 0
        self.last_agent_msg = ""
        self.last_sent_msg = ""
        self.dialogue_state = "opening"
        self.unresolved_demands = []
        self.confirmed_facts = []
        self.operator_claims = []
        self.contradictions = []
        self.latest_case_id = ""
        self.latest_case_outcome = ""
        self.follow_up_deadline = ""
        self.follow_up_anchor_at = ""
        self.last_event_at = ""
        self.pending_requested_field = ""

    def _rebuild_case_state_from_transcript(self):
        transcript = self._normalize_transcript_entries(self.transcript)
        if not transcript:
            return
        preserved_notes = list(self.operator_notes)
        preserved_saved_at = self.last_saved_at
        self._reset_derived_case_state()
        self.operator_notes = preserved_notes
        self.transcript = transcript
        for item in transcript:
            role = item.get("role") or ""
            content = (item.get("content") or "").strip()
            ts = (item.get("ts") or "").strip()
            if ts:
                self.last_event_at = ts
            if role == "agent":
                self.last_agent_msg = content
                self._update_case_memory(content, role="agent", persist=False)
            elif role == "customer_rep":
                self.last_sent_msg = self._strip_customer_echo(content)
                self._update_case_memory(self.last_sent_msg, role="customer_rep", persist=False)
        self._sync_message_count_from_transcript()
        self._sync_last_event_from_transcript()
        self._sync_follow_up_anchor_from_transcript()
        self.dialogue_state = self._infer_dialogue_state()
        self.last_saved_at = preserved_saved_at
        self._persist_case_memory()

    def _persist_case_memory(self):
        try:
            path = Path(self.case_memory_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._sync_message_count_from_transcript()
            self._sync_last_event_from_transcript()
            self._sync_follow_up_anchor_from_transcript()
            self._persist_full_transcript()
            payload = {
                "updated_at": self._utc_now_iso(),
                "store": self.store,
                "case_type": self.case_name(),
                "order_num": self.order_num or "",
                "customer_name": self.customer_name or "",
                "customer_email": self.customer_email or "",
                "customer_phone": self.customer_phone or "",
                "message_count": self.message_count,
                "last_agent_message": self.last_agent_msg or "",
                "last_customer_message": self.last_sent_msg or "",
                "dialogue_state": self.dialogue_state,
                "unresolved_demands": self.unresolved_demands[-12:],
                "confirmed_facts": self.confirmed_facts[-12:],
                "operator_claims": self.operator_claims[-12:],
                "contradictions": self.contradictions[-12:],
                "latest_case_id": self.latest_case_id,
                "latest_case_outcome": self.latest_case_outcome,
                "follow_up_deadline": self.follow_up_deadline,
                "follow_up_anchor_at": self.follow_up_anchor_at,
                "last_event_at": self.last_event_at,
                "operator_notes": self.operator_notes[-12:],
                "pending_requested_field": self.pending_requested_field,
                "transcript_count": len(self.transcript),
                "transcript_path": self.case_transcript_path,
                "transcript_tail": self._normalize_transcript_entries(self.transcript[-24:]),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.last_saved_at = payload["updated_at"]
        except Exception:
            pass

    def record_customer_message(self, message, source="manual"):
        message = self._strip_customer_echo((message or "").strip())
        if not message:
            return
        self.last_sent_msg = message
        self._append_transcript_entry("customer_rep", message)
        self._sync_message_count_from_transcript()
        self._update_case_memory(message, role="customer_rep")
        self._append_review_trace(
            "customer_message_sync",
            {
                "message": message,
                "source": source,
                "current_objective": self.current_objective(),
            },
        )

    def _append_review_trace(self, event_type, payload):
        try:
            trace_path = Path(self.review_trace_path)
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "event": event_type,
                "case_type": self.case_name(),
                "order_num": self.order_num or "",
                "dialogue_state": self.dialogue_state,
                "payload": payload,
            }
            with trace_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception:
            pass

    def record_agent_message(self, agent_text):
        agent_text = (agent_text or "").strip()
        if not agent_text:
            return
        self.last_agent_msg = agent_text
        self._append_transcript_entry("agent", agent_text)
        self._update_case_memory(agent_text, role="agent")
        self._append_review_trace(
            "agent_message",
            {
                "message": agent_text,
                "agent_intent": self.infer_agent_intent(agent_text),
                "current_objective": self.current_objective(),
                "contradictions": self.contradictions[-4:],
            },
        )
        self._persist_case_memory()

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
        self.last_sent_msg = self._strip_customer_echo((message or "").strip())
        self._update_case_memory(self.last_sent_msg, role="customer_rep")
        self._append_review_trace(
            "sent_message",
            {
                "message": self.last_sent_msg,
                "agent_intent": self.infer_agent_intent(self.last_agent_msg),
                "current_objective": self.current_objective(),
                "unresolved_demands": self.unresolved_demands[-4:],
            },
        )
        self._persist_case_memory()

    def _append_unique(self, bucket, item, limit=12):
        item = (item or "").strip()
        if not item:
            return
        normalized = self._normalize_message(item)
        existing = [self._normalize_message(x) for x in bucket]
        if normalized in existing:
            return
        bucket.append(item)
        if len(bucket) > limit:
            del bucket[:-limit]

    def _update_case_memory(self, text, role, persist=True):
        t = (text or "").strip()
        lowered = t.lower()
        if not t:
            return
        if role == "agent":
            self._append_unique(self.operator_claims, t)
            ids = self._extract_case_ids(t)
            if ids:
                self.latest_case_id = ids[-1].upper()
                self._append_unique(self.confirmed_facts, f"Case ID {self.latest_case_id} was provided by Lenovo")
            if any(x in lowered for x in ["na csat case manager", "escalation owner"]):
                self._append_unique(self.confirmed_facts, "Lenovo identified the current escalation owner")
            if any(x in lowered for x in ["refunds are processed after concession and case review is complete", "exact policy", "written policy"]):
                self._append_unique(self.confirmed_facts, "Lenovo provided its stated policy wording for the refund delay")
            if "there is no return policy that justifies withholding your refund" in lowered:
                self._append_unique(self.confirmed_facts, "Lenovo admitted there is no separate return policy basis for withholding the refund")
            if any(x in lowered for x in ["under review", "once approved", "after approval", "concession request"]):
                self._append_unique(self.confirmed_facts, "Lenovo said the refund depends on internal approval or concession review")
            if any(x in lowered for x in ["i do not have a firm deadline", "firm refund date depends", "no firm deadline"]):
                self._append_unique(self.operator_claims, "Lenovo said it does not have a firm deadline for refund approval")
            if any(x in lowered for x in ["24-48 business hours", "24 to 48 business hours", "contact us again", "reach out to us again"]):
                self._append_unique(self.confirmed_facts, "Lenovo asked for a 24-48 business hour follow-up window")
            if any(x in lowered for x in ["empty box", "box was empty"]):
                self._append_unique(self.operator_claims, "Lenovo claims the returned box was empty")
            if any(x in lowered for x in ["warehouse has not received", "not received the returned item", "not received the return"]):
                self._append_unique(self.operator_claims, "Lenovo claims the warehouse did not receive the return")
            if any(x in lowered for x in ["will be escalated", "escalated to the returns team"]):
                self._append_unique(self.confirmed_facts, "Lenovo said the case would be escalated to the returns team")
                self.latest_case_outcome = "Lenovo said the issue was escalated for further review."
            if any(x in lowered for x in ["i will need to escalate this issue", "i need to escalate this issue", "consider it as lost"]):
                self._append_unique(self.confirmed_facts, "Lenovo said the replacement issue would be escalated as a lost shipment")
                self.latest_case_outcome = "Lenovo said the replacement would be escalated as a lost shipment."
            if "48 hours" in lowered:
                self.follow_up_deadline = "48 hours"
                self.follow_up_anchor_at = self.last_event_at or self._utc_now_iso()
                self.latest_case_outcome = (
                    f"Lenovo opened case {self.latest_case_id} and asked for 48 hours to review the issue."
                    if self.latest_case_id else
                    "Lenovo asked for 48 hours to review the issue."
                )
            if any(x in lowered for x in ["5-7 business days", "processed within"]):
                self._append_unique(self.confirmed_facts, "Lenovo stated the refund would process within 5-7 business days after receiving the return")
                self.follow_up_deadline = "5-7 business days"
                self.follow_up_anchor_at = self.last_event_at or self._utc_now_iso()
                self.latest_case_outcome = "Lenovo said refunds are typically processed within 5-7 business days after receipt."
            if any(x in lowered for x in ["thank you for confirming", "thank you for staying connected", "sure, diana", "thank you, diana"]):
                self._append_unique(self.confirmed_facts, "Lenovo acknowledged the case is still under review")
        else:
            if any(x in lowered for x in ["written basis", "policy basis"]):
                self._append_unique(self.unresolved_demands, "written basis for withholding the refund")
            if "case id" in lowered:
                self._append_unique(self.unresolved_demands, "case ID confirmation and escalation status")
            if "timeline" in lowered or "24 hours" in lowered:
                self._append_unique(self.unresolved_demands, "written resolution timeline")
            if "returns team" in lowered or "escalat" in lowered:
                self._append_unique(self.unresolved_demands, "returns-team escalation")

        claims_text = " ".join(self.operator_claims).lower()
        if ("empty box" in claims_text or "box was empty" in claims_text) and (
            "warehouse did not receive the return" in claims_text or "not receive the return" in claims_text
        ):
            self._append_unique(
                self.contradictions,
                "Lenovo has said both that the box was empty and that the warehouse did not receive the return",
            )
        if "received back" in claims_text and (
            "warehouse did not receive the return" in claims_text or "not receive the return" in claims_text
        ):
            self._append_unique(
                self.contradictions,
                "Lenovo has said both that the return was received back and that the warehouse did not receive it",
            )
        if "refunds are withheld if a concession is under review" in claims_text and (
            "there is no return policy that justifies withholding your refund" in claims_text
        ):
            self._append_unique(
                self.contradictions,
                "Lenovo said the refund was being withheld during concession review, but later admitted there is no separate return policy basis for withholding it",
            )

        self.dialogue_state = self._infer_dialogue_state()
        if persist:
            self._persist_case_memory()

    def _infer_dialogue_state(self):
        intent = self.infer_agent_intent(self.last_agent_msg)
        if self.follow_up_deadline == "48 hours" and self.latest_case_id:
            return "case_opened_waiting"
        if self.message_count <= 1:
            return "opening"
        if intent in {"keepalive", "hold_request"}:
            return "holding"
        if intent in {"ups_redirect", "empty_box_claim", "warehouse_missing_claim", "supervisor_same_resolution"}:
            return "denial_or_deflection"
        if intent in {"case_id_provided", "escalation_confirmed"}:
            return "escalated_pending_timeline"
        if intent == "timeline_statement":
            return "timeline_offered"
        return "active_negotiation"

    def infer_agent_intent(self, text):
        t = self._operator_text(text).lower()
        if any(x in t for x in ["retail consumer or a small business", "small business or a retail consumer"]):
            return "consumer_type_question"
        if any(x in t for x in ["still connected", "checking in to confirm whether we are still connected"]):
            return "keepalive"
        if any(x in t for x in ["place this chat on hold", "please stay connected", "stay connected", "on hold for about", "stay online with me"]):
            return "hold_request"
        if any(x in t for x in ["go ahead and close the chat", "closing the chat", "close the chat", "haven't heard back from you"]):
            return "closure_warning"
        if (
            ("necessary for the original item to be returned" in t or "once we receive the original unit" in t)
            and ("refund" in t or "process the refund" in t)
        ):
            return "return_required_claim"
        if any(x in t for x in ["retrieve the order", "retrieve the original unit", "return it to lenovo", "return the original unit again"]):
            return "customer_retrieve_and_rereturn"
        if "sorry for any inconvenience" in t and any(x in t for x in ["best possible resolution", "surely check the details", "help you with"]):
            return "soft_stall"
        if any(x in t for x in ["contact ups", "reach out to ups", "ups drop-off center", "ups for the order confirmation"]):
            return "ups_redirect"
        if "chat transcript" in t:
            return "transcript_offer"
        if any(x in t for x in ["i understand your concern", "i understand, you would like", "i really appreciate your time"]):
            return "generic_empathy"
        if any(x in t for x in ["no further actions required from you", "there are no next steps you have to follow"]):
            return "no_action_required"
        if any(x in t for x in ["contact us again", "reach out to us again", "24-48 business hours", "24 to 48 business hours"]):
            return "callback_later"
        if any(x in t for x in ["same resolution", "same policies and have access to the same information"]):
            return "supervisor_same_resolution"
        if any(x in t for x in ["exact policy:", "written policy:", "refunds are processed after concession and case review is complete"]):
            return "policy_text_provided"
        if "there is no return policy that justifies withholding your refund" in t:
            return "no_policy_basis"
        if any(x in t for x in ["will be escalated", "escalated to the returns team", "i will need to escalate this issue", "i need to escalate this issue", "escalate this issue to our team"]):
            return "escalation_confirmed"
        if ("case id" in t or self._contains_case_id(t)) and any(x in t for x in ["here is", "shared", "raised", "confirmed"]):
            return "case_id_provided"
        if "empty box" in t or "box was empty" in t:
            return "empty_box_claim"
        if any(x in t for x in ["warehouse has not received", "not received the returned item", "not received the return"]):
            return "warehouse_missing_claim"
        if any(x in t for x in ["48 hours", "5-7 business days", "processed within", "resolution timeline"]):
            return "timeline_statement"
        if any(x in t for x in ["thank you for contacting", "anything else i can help you with today", "have a great day ahead"]):
            return "closing_polite"
        return "general"

    def current_objective(self):
        intent = self.infer_agent_intent(self.last_agent_msg)
        if intent == "consumer_type_question":
            return "answer the operator's classification question briefly, then keep the case moving"
        if intent == "keepalive":
            return "confirm connection and force a concrete next step"
        if intent == "hold_request":
            return "allow the hold briefly and require a concrete answer when the operator returns"
        if intent == "closure_warning":
            return "keep the chat open and force a concrete next action before the case is closed"
        if intent == "return_required_claim":
            return "state that tracking proves the return was already delivered and force warehouse verification plus refund timeline"
        if intent == "customer_retrieve_and_rereturn":
            return "reject the demand to retrieve and re-return an already delivered package and require Lenovo to verify internally"
        if intent == "soft_stall":
            return "convert empathy without action into a concrete refund status update and written timeline"
        if intent == "generic_empathy":
            return "turn empathy into a specific next step, missing fact, or deadline"
        if intent == "no_action_required":
            return "reject an open-ended callback loop and force the missing approval detail or written deadline"
        if intent == "callback_later":
            return "turn the callback window into a named owner, pending step, and written deadline"
        if intent == "supervisor_same_resolution":
            return "identify the real decision-maker and the deadline for approval instead of accepting a circular escalation"
        if intent == "policy_text_provided":
            return "move from policy wording to the specific pending approval step and its completion deadline"
        if intent == "no_policy_basis":
            return "lock in the admission and force Lenovo to state exactly what approval is still pending and when it ends"
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
        if intent == "timeline_statement":
            return "lock the promised review window to a concrete follow-up deadline or next update"
        if intent == "closing_polite":
            return "preserve the unresolved request in one short final message before the chat closes"
        if self.dialogue_state == "case_opened_waiting":
            return "preserve the case record and resume after the promised 48-hour review window if Lenovo does not resolve it"
        return "push toward refund, escalation, written basis, and timeline"

    def _deterministic_reply_intents(self):
        return {
            "consumer_type_question",
            "hold_request",
            "keepalive",
            "soft_stall",
            "generic_empathy",
            "closure_warning",
            "escalation_confirmed",
            "no_action_required",
            "callback_later",
            "policy_text_provided",
            "no_policy_basis",
            "supervisor_same_resolution",
            "closing_polite",
            "transcript_offer",
        }

    def plan_next_action(self, agent_text="", observation=None, first_turn=False):
        observation = observation or {}
        transcript_window = self._planning_transcript_window()
        intent = self.infer_agent_intent(agent_text)
        if (
            agent_text
            and not first_turn
            and observation.get("chat_ready", True)
            and intent in self._deterministic_reply_intents()
        ):
            message = self._polish_chat_reply(
                self._fallback_message(agent_text=agent_text, first_turn=first_turn),
                agent_text=agent_text,
                first_turn=first_turn,
            )
            return {
                "action": "send_message" if message else "wait",
                "message": message,
                "goal": self.current_objective(),
                "reason": f"deterministic_{intent}",
                "confidence": 0.85 if message else 0.0,
            }
        user_prompt = {
            "case": self.build_case_snapshot(),
            "first_turn": bool(first_turn),
            "latest_agent_message": agent_text or "",
            "observation": observation,
            "transcript_tail": transcript_window[-8:],
            "transcript_window": transcript_window,
            "transcript_message_count": len(self.transcript),
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
                fallback_message = self._polish_chat_reply(
                    self._fallback_message(agent_text=agent_text, first_turn=first_turn),
                    agent_text=agent_text,
                    first_turn=first_turn,
                )
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
        raw_message = self._polish_chat_reply(
            self._sanitize_reply(plan.get("message") or ""),
            agent_text=agent_text,
            first_turn=first_turn,
        ) if action == "send_message" else ""
        message = raw_message
        precheck_reason = ""
        if action == "send_message" and self._looks_like_role_inversion(message):
            precheck_reason = "role_inversion"
            message = self._polish_chat_reply(
                self._fallback_message(agent_text=agent_text, first_turn=first_turn),
                agent_text=agent_text,
                first_turn=first_turn,
            )
        if action == "send_message" and not self._message_addresses_intent(message, agent_text):
            precheck_reason = precheck_reason or "intent_mismatch"
            message = self._polish_chat_reply(
                self._fallback_message(agent_text=agent_text, first_turn=first_turn),
                agent_text=agent_text,
                first_turn=first_turn,
            )
        message_before_critic = message
        if action == "send_message":
            message = self._critic_pass(agent_text, message, observation, first_turn)
            message = self._polish_chat_reply(message, agent_text=agent_text, first_turn=first_turn)
            if isinstance(self.last_critic_verdict, dict):
                self.last_critic_verdict["final_message"] = message
        self._append_review_trace(
            "reply_plan",
            {
                "first_turn": bool(first_turn),
                "agent_message": agent_text or "",
                "agent_intent": self.infer_agent_intent(agent_text),
                "observation": observation,
                "action": action,
                "goal": (plan.get("goal") or "").strip(),
                "reason": (plan.get("reason") or "").strip(),
                "confidence": float(plan.get("confidence") or 0.0),
                "draft_initial": raw_message,
                "draft_after_rules": message_before_critic,
                "precheck_reason": precheck_reason,
                "critic": self.last_critic_verdict,
                "final_message": message,
            },
        )
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
        case_ref = self.active_case_reference()
        pressure = self.legal_pressure_level()
        intent = self.infer_agent_intent(agent_text)
        next_asks = self.next_best_asks(agent_text)
        if first_turn:
            if (self.case_type or "").upper() in {"DOA", "DAMAGED", "DEFECTIVE", "BROKEN"}:
                return (
                    f"I need help with order {order}. "
                    "I received a laptop with a broken screen, Lenovo arranged a replacement, I never received it, and I already returned the defective unit using Lenovo's UPS label. "
                    "Please confirm the full refund and the exact processing timeline today."
                )
            return (
                f"I need help with order {order}. "
                f"The issue is that {issue}. "
                "Please review the case and confirm the fastest resolution available today."
            )

        if intent == "consumer_type_question":
            return "I am a retail consumer."
        if self._explicit_field_request(agent_text, "email"):
            return f"The email on the order is {normalize_customer_email(self.customer_email)}."
        if self._explicit_field_request(agent_text, "phone"):
            return f"The phone number on the order is {normalize_customer_phone(self.customer_phone)}."
        if self._explicit_field_request(agent_text, "order"):
            return f"The order number is {normalize_order_num(self.order_num)}."
        if self._explicit_field_request(agent_text, "name"):
            return f"My name is {normalize_customer_name(self.customer_name)}."
        if intent == "keepalive":
            return (
                "Yes, I am still here. "
                f"Please confirm whether {case_ref} has already been escalated and tell me the exact next step and timeline."
            )
        if intent == "hold_request":
            ask = next_asks[0] if next_asks else "tell me the exact next step and timeline"
            ask = re.sub(r"^when you return,\s*", "", ask, flags=re.I)
            return (
                "Yes, that's fine. "
                f"When you return, please {ask}."
            )
        if intent == "closure_warning":
            ask = next_asks[0] if next_asks else "confirm the refund status or the next update deadline"
            return (
                "I am still here. "
                f"Before the chat closes, please {ask}."
            )
        if intent == "escalation_confirmed":
            ask = next_asks[0] if next_asks else "confirm the written resolution timeline"
            return (
                "Thank you for confirming the escalation. "
                f"Please {ask} today."
            )
        if intent == "return_required_claim":
            return (
                "The tracking already shows the original defective laptop was delivered back to Lenovo, so this should not still be treated as an unreceived return. "
                "Please verify this with your warehouse and confirm the refund status plus the written refund timeline today."
            )
        if intent == "customer_retrieve_and_rereturn":
            return (
                "I should not be asked to retrieve and re-return a package that tracking already shows was delivered to Lenovo. "
                "Please verify the discrepancy internally with your warehouse and confirm in writing whether Lenovo has the return, along with the refund timeline today."
            )
        if intent in {"soft_stall", "generic_empathy"}:
            ask = next_asks[0] if next_asks else "give me the specific next step and the timeline"
            return (
                "I need a concrete update rather than a general assurance. "
                f"Please {ask}."
            )
        if intent == "ups_redirect":
            return (
                "The return used Lenovo's UPS label, so Lenovo should coordinate with UPS internally if Lenovo is disputing the contents of the return. "
                f"Please keep {case_ref} escalated with the returns team and confirm the written basis for withholding the refund plus the exact resolution timeline today."
            )
        if intent == "warehouse_missing_claim":
            return (
                "Your updates are inconsistent because Lenovo previously stated that the return was received back, and now you are stating that the warehouse did not receive it. "
                "Please escalate this discrepancy to the returns team today, provide the case ID for that escalation, and confirm in writing whether Lenovo is treating this as a lost return or an empty-box claim."
            )
        if intent == "transcript_offer":
            return (
                "A transcript is fine, but I also need the escalation owner and the exact next update deadline before we end this chat."
            )
        if intent == "closing_polite":
            ask = next_asks[0] if next_asks else "confirm the next written update deadline"
            return f"Before we end, please {ask}."
        if intent == "empty_box_claim":
            return (
                f"The return for order {order} was sent using Lenovo's UPS label and Lenovo's own update states that the return was received back. "
                "If Lenovo is asserting an empty-box exception, please escalate this to the returns team today, provide the case ID, and confirm the written basis for withholding the refund."
            )
        if intent == "callback_later":
            ask = next_asks[0] if next_asks else "confirm what exact step is still pending and who owns the follow-up"
            return (
                "Before I wait another 24-48 business hours, I need one specific update. "
                f"Please {ask}."
            )
        if intent == "no_action_required":
            ask = next_asks[0] if next_asks else "tell me what exact approval step is still pending"
            return (
                "I understand you say there is nothing else for me to do. "
                f"Please {ask} before I wait another 24-48 business hours."
            )
        if intent == "supervisor_same_resolution":
            ask = next_asks[0] if next_asks else "tell me who can approve the refund and when that review will finish"
            return (
                "If the supervisor cannot change the outcome, then I need the real decision-maker. "
                f"Please {ask}."
            )
        if intent == "policy_text_provided":
            asks = next_asks[:2] or ["state what exact approval step is still pending", "give the deadline for when that review will finish"]
            return (
                f"You already provided Lenovo's policy wording for {case_ref}. "
                f"Please {asks[0]}, and {asks[1]}."
            )
        if intent == "no_policy_basis":
            asks = next_asks[:2] or ["state what exact approval step is still pending", "give the deadline for when that review will finish"]
            return (
                "You just confirmed there is no separate return policy basis for withholding my refund. "
                f"Please {asks[0]}, and {asks[1]}."
            )
        t = self._operator_text(agent_text).lower()
        if any(x in t for x in ["ups", "receipt", "drop-off", "drop off", "empty box"]):
            return (
                f"The return for order {order} was sent using Lenovo's UPS label and Lenovo's own update states that the return was received back. "
                "If Lenovo is asserting an empty-box exception, please escalate this to the returns team today, provide the case ID, and confirm the written basis for withholding the refund."
            )
        if any(x in t for x in ["case id", "ticket", "reference"]):
            if self.latest_case_id:
                return f"The case ID is {self.latest_case_id}. Please use it to review the case and confirm the escalation timeline in writing today."
            return "Please provide the case ID and confirm the escalation timeline in writing today."
        if any(x in t for x in ["cannot", "unable", "policy", "denied", "decline"]):
            if pressure == "high":
                return (
                    "If Lenovo is refusing the refund, please provide the written policy basis and supervisor escalation today. "
                    "If this purchase was paid by credit card, I will also preserve my billing-dispute rights, so I need the case ID and written timeline now."
                )
            return (
                "Please escalate this to a supervisor or escalations team and provide the policy basis in writing today. "
                "Also confirm the case ID and deadline for resolution."
            )
        if (self.case_type or "").upper() in {"DOA", "DAMAGED", "DEFECTIVE", "BROKEN"}:
            if pressure == "high":
                return (
                    f"Tracking already shows Lenovo received the return for order {order}, and I am not agreeing to an open-ended delay. "
                    "Please confirm the full refund now or provide the written basis, escalation owner, and exact refund timeline today."
                )
            return (
                f"Tracking already shows Lenovo received the return for order {order}, so the refund should not remain outstanding. "
                "Please confirm whether you will complete the full refund now or escalate this to the refunds team today with a case ID and timeline."
            )
        if pressure == "high":
            ask = next_asks[0] if next_asks else "provide the exact deadline so I can preserve my dispute rights"
            return (
                f"I need a concrete resolution on order {order} today. "
                f"If this remains unresolved, please {ask}."
            )
        if next_asks:
            return f"I need one specific update on order {order}. Please {next_asks[0]}."
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

    def _critic_pass(self, agent_text, draft, observation=None, first_turn=False):
        draft = (draft or "").strip()
        self.last_critic_verdict = {
            "approved": True,
            "source": "draft",
            "final_message": draft,
        }
        if not draft:
            return draft
        if self._looks_like_role_inversion(draft):
            fix = self._fallback_message(agent_text=agent_text, first_turn=first_turn)
            self.last_critic_verdict = {
                "approved": False,
                "source": "precheck_role_inversion",
                "fix": fix,
                "final_message": fix,
            }
            return fix
        if not self._message_addresses_intent(draft, agent_text):
            fix = self._fallback_message(agent_text=agent_text, first_turn=first_turn)
            self.last_critic_verdict = {
                "approved": False,
                "source": "precheck_intent_mismatch",
                "fix": fix,
                "final_message": fix,
            }
            return fix
        try:
            critic_input = {
                "case": self.build_case_snapshot(),
                "latest_agent_message": agent_text or "",
                "draft_reply": draft,
                "observation": observation or {},
            }
            raw = self._call_llm(
                system_prompt=REPLY_CRITIC_PROMPT,
                history=[{"role": "user", "content": json.dumps(critic_input, ensure_ascii=True)}],
                temperature=0.0,
                sanitize=False,
            )
            verdict = self._extract_json_object(raw) or {}
            approved = bool(verdict.get("approved"))
            fix = self._sanitize_reply(verdict.get("fix") or "")
            self.last_critic_verdict = {
                "approved": approved,
                "source": "llm_critic",
                "fix": fix,
                "raw_verdict": verdict,
                "final_message": draft,
            }
            if approved:
                return draft
            if fix and not self._looks_like_role_inversion(fix) and self._message_addresses_intent(fix, agent_text):
                self.last_critic_verdict["final_message"] = fix
                return fix
        except Exception as e:
            self.last_critic_verdict = {
                "approved": True,
                "source": "critic_error",
                "error": str(e),
                "final_message": draft,
            }
        return draft

    def _call_llm(self, system_prompt=None, history=None, temperature=None, sanitize=True, max_tokens=None):
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        system_prompt = system_prompt or SYSTEM_PROMPT
        history = history if history is not None else self.history
        payload = {
            "model": OPENAI_MODEL,
            "messages": [{"role": "system", "content": system_prompt}] + history,
            "max_tokens": 700 if max_tokens is None else max_tokens,
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
        return self._enforce_first_person(t)

    def _normalize_case_id_mentions(self, text):
        t = (text or "").strip()
        if not t:
            return ""
        if self.latest_case_id:
            return re.sub(self._case_id_pattern(), self.latest_case_id, t, flags=re.I)
        t = re.sub(rf"\bcase id\s+{self._case_id_pattern()}\b", "this case", t, flags=re.I)
        return re.sub(self._case_id_pattern(), "this case", t, flags=re.I)

    def _dedupe_sentences(self, text):
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p.strip()]
        if not parts:
            return ""
        seen = set()
        result = []
        for part in parts:
            key = self._normalize_message(part)
            if key in seen:
                continue
            seen.add(key)
            result.append(part)
        return " ".join(result[:4]).strip()

    def _polish_chat_reply(self, text, agent_text="", first_turn=False):
        t = self._enforce_first_person(text)
        if not t:
            return ""
        t = self._normalize_case_id_mentions(t)
        replacements = [
            (r"\bI am assisting with a case where\s+", ""),
            (r"\bI am assisting with this case\.?\s*", ""),
            (r"\bI am reaching out regarding\b", "I need help with"),
            (r"\bkindly\b", "please"),
            (r"\bwe need\b", "I need"),
            (r"\bwe are still connected\b", "I am still here"),
            (r"\bwe are still here\b", "I am still here"),
            (r"\bCould you please\b", "Please"),
            (r"\bCan you please\b", "Please"),
        ]
        for pattern, repl in replacements:
            t = re.sub(pattern, repl, t, flags=re.I)
        if not first_turn:
            t = re.sub(r"^\s*(hello|hi)[,\s]+", "", t, flags=re.I)
            t = re.sub(r"^\s*thank you for your message\.\s*", "", t, flags=re.I)
            t = re.sub(r"^\s*thank you for the information\.\s*", "", t, flags=re.I)
        t = re.sub(r"(^|[.!?]\s+)(please)\b", lambda m: f"{m.group(1)}Please", t, flags=re.I)
        t = re.sub(r"\s+", " ", t).strip(" ,")
        if t:
            t = t[0].upper() + t[1:]
        return self._dedupe_sentences(t)

    def _enforce_first_person(self, text):
        t = (text or "").strip()
        if not t:
            return ""
        replacements = [
            (r"\bthe customer name is\b", "My name is"),
            (r"\bthe customer returned\b", "I returned"),
            (r"\bthe customer has not\b", "I have not"),
            (r"\bthe customer did not\b", "I did not"),
            (r"\bthe customer needs\b", "I need"),
            (r"\bthe customer is\b", "I am"),
            (r"\bthe customer\b", "I"),
            (r"\bthe buyer\b", "I"),
            (r"\bthe account holder\b", "I"),
        ]
        for pattern, repl in replacements:
            t = re.sub(pattern, repl, t, flags=re.I)
        t = re.sub(r"\bI returned the defective laptop using Lenovo's label\b", "I returned the defective laptop using Lenovo's UPS label", t, flags=re.I)
        t = re.sub(r"\bwe need\b", "I need", t, flags=re.I)
        return re.sub(r"\s+", " ", t).strip()

    def _normalize_message(self, text):
        stripped = self._strip_customer_echo(text or "")
        return re.sub(r"\s+", " ", stripped.strip().lower())

    def _message_addresses_intent(self, message, agent_text):
        msg = self._normalize_message(message)
        intent = self.infer_agent_intent(agent_text)
        if not msg:
            return False
        if "the customer" in msg or "the buyer" in msg:
            return False
        requested_field = self._requested_customer_field(agent_text)
        if requested_field == "email":
            return "@" in message
        if requested_field == "phone":
            return bool(re.search(r"\d", message)) and "phone" in msg
        if requested_field == "order":
            return bool(re.search(r"\d", message)) and "order" in msg
        if requested_field == "name":
            return msg.startswith("my name is") or "name is" in msg
        if intent == "consumer_type_question":
            return "retail consumer" in msg or "small business" in msg
        if intent == "keepalive":
            return "still here" in msg or "connected" in msg or msg.startswith("yes")
        if intent == "hold_request":
            return msg.startswith("yes") or "that's fine" in msg or "when you return" in msg
        if intent == "closure_warning":
            return ("still here" in msg or "chat closes" in msg or msg.startswith("i am still")) and (
                "refund" in msg or "escalat" in msg or "next update" in msg
            )
        if intent == "return_required_claim":
            return ("tracking" in msg or "delivered" in msg) and ("verify" in msg or "refund" in msg)
        if intent == "customer_retrieve_and_rereturn":
            return ("tracking" in msg or "delivered" in msg or "already" in msg) and ("verify" in msg or "warehouse" in msg or "discrepancy" in msg)
        if intent == "soft_stall":
            return "concrete" in msg or "timeline" in msg or "refund status" in msg
        if intent == "generic_empathy":
            return "specific" in msg or "next step" in msg or "pending" in msg or "timeline" in msg
        if intent == "no_action_required":
            return "before i wait" in msg or "pending" in msg or "approval" in msg
        if intent == "callback_later":
            return "24-48" in msg or "owner" in msg or "pending" in msg or "update" in msg
        if intent == "supervisor_same_resolution":
            return "decision-maker" in msg or "approve" in msg or "authority" in msg
        if intent == "policy_text_provided":
            return "approval" in msg or "pending" in msg or "deadline" in msg or "when" in msg
        if intent == "no_policy_basis":
            return "no separate return policy" in msg or "approval" in msg or "pending" in msg or "deadline" in msg
        if intent == "ups_redirect":
            return "ups" in msg and ("internally" in msg or "returns team" in msg or "escalat" in msg)
        if intent == "transcript_offer":
            return "transcript" in msg or "timeline" in msg or "escalat" in msg
        if intent == "case_id_provided":
            case_id = (self.latest_case_id or "").lower()
            return (case_id and case_id in msg) or self._contains_case_id(message) or "case id" in msg or "timeline" in msg
        if intent == "escalation_confirmed":
            return "timeline" in msg or "written" in msg or "empty-box" in msg or "lost return" in msg
        if intent == "empty_box_claim":
            return "empty box" in msg or "written basis" in msg or "returns team" in msg
        if intent == "warehouse_missing_claim":
            return "inconsistent" in msg or "received back" in msg or "lost return" in msg
        if intent == "timeline_statement":
            return "timeline" in msg or "update" in msg or "48 hours" in msg or "follow up" in msg
        if intent == "closing_polite":
            return "before we end" in msg or "next update" in msg or "deadline" in msg
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
            "i am assisting with a case",
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
            "the customer",
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
        if "lenovo.com/us/vipmembers/ticketsatwork/en/contact/order-support" in url:
            score += 500
        elif "lenovo.com" in url:
            score += 60
        if "account.lenovo.com" in url:
            score -= 120
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
                "advisor is typing",
                "agent is typing",
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
                "is typing",
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

async def read_last_customer_message(page, store):
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        def strip_echo(text):
            t = (text or "").strip()
            if t.lower().startswith("your message"):
                remainder = re.sub(r"^your message[\s:,-]*", "", t, flags=re.I).strip()
                return remainder or t
            return t

        def looks_like_noise(text):
            t = re.sub(r"\s+", " ", strip_echo(text).strip().lower())
            if not t or len(t) < 2:
                return True
            blocked = [
                "type your message here",
                "advisor is typing",
                "agent is typing",
                "chat with us",
                "existing orders",
                "general question",
                "operator",
                "consumer",
            ]
            return any(x == t for x in blocked)

        frames = [page.main_frame] + list(page.frames)
        for frame in frames:
            try:
                all_elements = await frame.query_selector_all(sel["messages"])
            except Exception:
                continue
            for el in reversed(all_elements):
                try:
                    text = strip_echo((await el.inner_text()).strip())
                    if looks_like_noise(text):
                        continue
                    cls = (await el.get_attribute("class") or "").lower()
                    is_ours = any(w in cls for w in ["visitor", "customer", "user", "outgoing", "sent"])
                    if is_ours:
                        return text
                except Exception:
                    continue
        return None
    except Exception:
        return None

async def type_message(page, store, text):
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        if store == "lenovo.com":
            try:
                visible_state = await detect_lenovo_visible_state(page)
                widget_text = (await get_lenovo_widget_text(page)).lower()
                widget_state = visible_state or classify_lenovo_widget_state(widget_text)
                has_live_chat_input = False
                for frame in [page.main_frame] + list(page.frames):
                    try:
                        live_input = await frame.query_selector(
                            "textarea#chatInput:visible, textarea[placeholder='Type your message here']:visible, .cx-input textarea:visible"
                        )
                        if live_input:
                            has_live_chat_input = True
                            break
                    except Exception:
                        continue
                if widget_state in {"name", "email", "phone", "order", "existing_pick", "general_pick", "operator_pick", "consumer_pick"} and not has_live_chat_input:
                    return False
            except Exception:
                pass
            frames = [page.main_frame] + list(page.frames)
            for frame in frames:
                try:
                    ok = await frame.evaluate(
                        """
                        (msg) => {
                          const visible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const st = window.getComputedStyle(el);
                            return r.width > 8 && r.height > 8 && st.visibility !== "hidden" && st.display !== "none";
                          };
                          const candidates = [
                            document.querySelector("#chatInput"),
                            document.querySelector("textarea[placeholder='Type your message here']"),
                            document.querySelector(".cx-input textarea"),
                            ...Array.from(document.querySelectorAll("#insideWorkflowFieldCell input[aria-label], #insideWorkflowFieldCell textarea[aria-label]"))
                              .filter((el) => {
                                const aria = (el.getAttribute("aria-label") || "").toLowerCase();
                                return visible(el) && aria.includes("how can we help you today") && !/\\(\\d+ of \\d+\\)/.test(aria);
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
                          const input = document.querySelector("#chatInput, textarea[placeholder='Type your message here'], .cx-input textarea");
                          if (!input) return false;
                          const value = (input.value || input.textContent || "").trim();
                          if (!value) return false;
                          const btn = document.querySelector("#chatSendButton, button.cx-send, [class*='cx-send'], [aria-label*='Send' i], button[type='submit']");
                          if (btn) {
                            const sig = ((btn.className || "") + " " + (btn.getAttribute("aria-label") || "")).toLowerCase();
                            if (!sig.includes("disabled")) {
                              btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
                              btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
                              btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
                              if (typeof btn.click === "function") btn.click();
                              return true;
                            }
                          }
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
                try:
                    sent = await frame.evaluate(
                        """
                        () => {
                          const visible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const st = window.getComputedStyle(el);
                            return r.width > 8 && r.height > 8 && st.visibility !== "hidden" && st.display !== "none";
                          };
                          const input = Array.from(document.querySelectorAll("#insideWorkflowFieldCell input[aria-label], #insideWorkflowFieldCell textarea[aria-label], input[aria-label], textarea[aria-label]"))
                            .find((el) => {
                              const aria = (el.getAttribute("aria-label") || "").toLowerCase();
                              return visible(el) && aria.includes("how can we help you today") && !/\\(\\d+ of \\d+\\)/.test(aria);
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
        "#inside_liveChatTab",
        "#contactServiceContainer",
        "#or_chat_customer",
        "#contactBusinessSalesContainer",
        "#or_chat_smb",
        "#contactServiceLink",
        "#contactServiceLinkInfo",
    ]
    for sel in ("#contactServiceContainer", "#or_chat_customer", "#inside_liveChatTab"):
        try:
            await page.wait_for_selector(sel, state="visible", timeout=3500)
            loc = page.locator(sel).first
            if await loc.is_visible():
                await loc.click(force=True)
                print(f"  ℹ️  Lenovo CTA click via waited locator: {sel}")
                await page.wait_for_timeout(700)
                return True
        except Exception:
            continue
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
                el = await frame.query_selector(sel)
                if el and await el.is_visible():
                    box = await el.bounding_box()
                    if box:
                        try:
                            await el.click(force=True)
                        except Exception:
                            await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        print(f"  ℹ️  Lenovo CTA click via direct element: {sel}")
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
    for sel in priority_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                await loc.click(force=True)
                print(f"  ℹ️  Lenovo CTA click via page locator: {sel}")
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
    try:
        widget_text = await get_lenovo_widget_text(page)
    except Exception:
        widget_text = ""

    lowered = (widget_text or "").lower()
    if "chat with an agent" in lowered:
        if await click_lenovo_picklist_option(page, ["Chat with an Agent"]):
            print("  ✅ Lenovo step: Chat with an Agent (recovery)")
            await page.wait_for_timeout(700)
            return True

    if "start a new chat" in lowered:
        if await click_lenovo_picklist_option(page, ["START A NEW CHAT", "Start a new chat"]):
            print("  ✅ Lenovo step: Start a new chat")
            await page.wait_for_timeout(700)
            try:
                widget_text = await get_lenovo_widget_text(page)
            except Exception:
                widget_text = ""
            if "chat with an agent" in (widget_text or "").lower():
                if await click_lenovo_picklist_option(page, ["Chat with an Agent"]):
                    print("  ✅ Lenovo step: Chat with an Agent (after restart)")
                    await page.wait_for_timeout(700)
            return True

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
    return await reset_lenovo_widget(page)

async def reset_lenovo_widget(page):
    """
    Жёстко закрывает текущий Lenovo widget и открывает его заново через outer CTA.
    Нужен для случаев, когда Powerfront сохраняет старый transcript между вкладками/рестартами профиля.
    """
    try:
        await page.evaluate(
            """
            () => {
              const visible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return r.width > 6 && r.height > 6 && st.display !== "none" && st.visibility !== "hidden";
              };

              const closers = Array.from(document.querySelectorAll(
                "#insideCloseButton, #inside_close_button, [aria-label*='close' i], [title*='close' i], .closeChat, .chatClose, #inside_holder .close"
              )).filter(visible);

              for (const el of closers) {
                try {
                  const r = el.getBoundingClientRect();
                  el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                  el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
                  if (typeof el.click === "function") el.click();
                } catch (_) {}
              }

              const pane = document.querySelector("#insideChatPane");
              if (pane) {
                pane.classList.add("closed");
                pane.classList.remove("open");
              }
              const holder = document.querySelector("#inside_holder");
              if (holder) holder.classList.remove("chatPaneOpen");
              const iframe = document.querySelector("#insideChatFrame");
              if (iframe) {
                iframe.style.pointerEvents = "none";
                iframe.style.visibility = "hidden";
              }
              return true;
            }
            """
        )
    except Exception:
        pass

    try:
        await page.wait_for_timeout(700)
    except Exception:
        pass

    reopened = await click_lenovo_contact_chat_cta(page)
    if reopened:
        print("  ✅ Lenovo step: Widget reset via close/reopen")
        try:
            await page.wait_for_timeout(900)
        except Exception:
            pass
        return True
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
                  const score = (el, text) => {
                    let s = 0;
                    const cls = String(el.className || "").toLowerCase();
                    if (cls.includes("picklistoption")) s += 100;
                    if (cls.includes("picklistcontent")) s += 60;
                    if (el.getAttribute("role") === "listitem") s += 40;
                    if ((el.getAttribute("aria-label") || "").trim()) s += 20;
                    s -= Math.max(0, text.length - 40);
                    return s;
                  };
                  const cands = Array.from(document.querySelectorAll(".picklistOption, .picklistOptionLink, .picklistContent, .text, span, div, a"))
                    .filter((el) => visible(el) && inWidget(el));
                  const matches = [];
                  for (const el of cands) {
                    const text = norm((el.innerText || "").slice(0, 120));
                    const aria = norm((el.getAttribute("aria-label") || "").slice(0, 120));
                    const candidateText = text || aria;
                    if (!candidateText || candidateText.length > 80) continue;
                    if (!wanted.has(candidateText)) continue;
                    matches.push({ el, score: score(el, candidateText) });
                  }
                  matches.sort((a, b) => b.score - a.score);
                  for (const item of matches) {
                    const el = item.el;
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
        "button:has-text('Chat Live Now')",
        "a:has-text('Chat Live Now')",
        "[aria-label*='Chat Live Now' i]",
        "[title*='Chat Live Now' i]",
        "[class*='chat' i]:has-text('Chat Live Now')",
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
                    .filter((el) => visible(el) && bottom(el) && /chat\\s*(live\\s*)?now/i.test((el.innerText || "") + " " + (el.getAttribute("aria-label") || "")));
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
        "button:has-text('Chat Live Now')",
        "a:has-text('Chat Live Now')",
        "[aria-label*='Chat Live Now' i]",
        "[title*='Chat Live Now' i]",
        "[class*='chat' i]:has-text('Chat Live Now')",
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
                        .some(el => visible(el) && /chat\\s*(live\\s*)?now/i.test((el.innerText || "") + " " + (el.getAttribute("aria-label") || "")));
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
        try:
            await page.wait_for_timeout(500)
        except Exception:
            return False
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
    Возвращает текст именно Lenovo chat widget, а не общей страницы order-support.
    Сначала читаем дочерние frames, затем main frame. Fallback по body разрешаем
    только если в тексте есть явные маркеры Lenovo advisor/chat workflow.
    """
    frames = list(page.frames)
    if page.main_frame not in frames:
        frames.append(page.main_frame)
    for frame in frames:
        try:
            txt = await frame.evaluate(
                """
                () => {
                  const visible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 8 && r.height > 8 && st.display !== "none" && st.visibility !== "hidden";
                  };
                  const meta = [];
                  const pane = document.querySelector("#insideChatPane");
                  if (pane && visible(pane) && pane.classList.contains("expired")) meta.push("__chat_expired__");
                  const workflowCell = document.querySelector("#insideWorkflowFieldCell");
                  if (workflowCell && visible(pane) && getComputedStyle(workflowCell).display === "none") meta.push("__workflow_hidden__");
                  const allRoots = Array.from(document.querySelectorAll(
                    "#insideChatPane, #inside_holder, #insideChatFrame, [class*='cx-widget'], [id*='cx-container'], .cx-webchat, [class*='webchat' i], [class*='genesys' i], .workflowBubble, .picklist"
                  ));
                  const roots = allRoots.filter(visible);
                  const chunks = roots
                    .map((el) => (el.innerText || "").replace(/\\s+/g, " ").trim())
                    .filter(Boolean)
                    .sort((a, b) => b.length - a.length);
                  if (!chunks.length) return "";
                  const bodyTxt = chunks[0] || "";
                  return `${meta.join(" ")} ${bodyTxt}`.trim();
                }
                """
            )
            if txt:
                return txt
        except Exception:
            continue
        try:
            txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.replace(/\\s+/g, ' ').trim() : ''")
            lowered = (txt or "").lower()
            if txt and any(
                marker in lowered
                for marker in (
                    "advisor message",
                    "your message",
                    "type your message here",
                    "existing orders",
                    "general question",
                    "virtual assistant",
                    "speak with an operator",
                    "retail consumer or a small business",
                    "what's your name",
                    "email address",
                    "phone number",
                    "order number",
                    "start a new chat",
                    "chat has been disconnected",
                    "trying to reconnect",
                )
            ):
                return txt
        except Exception:
            continue
    return ""

def classify_lenovo_widget_state(text):
    t = (text or "").lower()
    if not t:
        return "unknown"
    top_menu_visible = (
        "existing orders" in t
        and ("technical support" in t or "new order / product" in t or "chat via whatsapp" in t)
    )
    if top_menu_visible and "__workflow_hidden__" in t:
        return "existing_pick"
    if (
        "chat has been disconnected" in t
        or "trying to reconnect" in t
    ):
        return "restart"
    if "__chat_expired__" in t and not top_menu_visible:
        return "restart"
    step_positions = []

    def add_step(state, *markers):
        pos = max((t.rfind(marker) for marker in markers if marker), default=-1)
        if pos >= 0:
            step_positions.append((pos, state))

    add_step("restart", "start a new chat")
    add_step("agent_entry", "chat with an agent", "click below to chat with an agent")
    add_step("name", "what's your name", "customer name")
    add_step("email", "email address", "correct email format")
    add_step("phone", "phone number")
    add_step("order", "order number")
    add_step("operator_pick", "would you like to continue with our virtual assistant or speak with an operator")
    add_step("consumer_pick", "retail consumer or a small business")
    add_step("general_pick", "general question")
    add_step("chat_ready", "type your message here")

    if re.search(r"[a-z0-9][a-z0-9 .,'-]{1,80}, how can we help you today\\?", t):
        step_positions.append((t.rfind("how can we help you today?"), "chat_ready"))

    if step_positions:
        step_positions.sort(key=lambda item: item[0])
        latest_state = step_positions[-1][1]
        if latest_state != "general_pick":
            return latest_state
    if (
        "existing orders" in t
        and ("technical support" in t or "new order / product" in t or "chat via whatsapp" in t)
        and "type your message here" not in t
    ):
        return "existing_pick"
    if "how can we help you today?" in t and "general question" in t:
        return "general_pick"
    if "welcome to lenovo! how can we help you today?" in t and "existing orders" in t:
        return "existing_pick"
    if "how can we help you today?" in t and "type your message here" in t:
        return "chat_ready"
    return "unknown"

async def is_lenovo_widget_open(page):
    """
    Проверка, открыт ли уже Lenovo chat widget (чтобы не кликать launcher повторно и не закрывать его).
    """
    try:
        widget_text = await get_lenovo_widget_text(page)
        visible_state = await detect_lenovo_visible_state(page)
        if visible_state in {
            "agent_entry",
            "existing_pick",
            "general_pick",
            "operator_pick",
            "consumer_pick",
            "name",
            "email",
            "phone",
            "order",
            "chat_ready",
            "restart",
        }:
            return True
        if widget_text and classify_lenovo_widget_state(widget_text) in {
            "agent_entry",
            "existing_pick",
            "general_pick",
            "operator_pick",
            "consumer_pick",
            "name",
            "email",
            "phone",
            "order",
            "chat_ready",
            "restart",
        }:
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
                  const root = document.querySelector("#insideChatPane, #inside_holder, #insideChatFrame");
                  const txt = (root && root.innerText ? root.innerText : "").toLowerCase();
                  return txt.includes("lenovo online sales support")
                    || txt.includes("how can we help you today")
                    || txt.includes("would you like to continue with our virtual assistant")
                    || txt.includes("existing orders");
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

def normalize_lenovo_phone(value):
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return digits

def normalize_order_num(value):
    return re.sub(r"\s+", "", (value or "").strip())

async def fill_lenovo_advisor_step(page, session, forced_state=None):
    """
    LenovoAdvisor часто показывает одно поле на шаг.
    Смотрим текст текущего шага и вбиваем соответствующее значение.
    """
    frames = [page.main_frame] + [fr for fr in page.frames if fr != page.main_frame]
    state = forced_state or classify_lenovo_widget_state(await get_lenovo_widget_text(page))
    expected_next = {
        "name": {"email"},
        "email": {"phone"},
        "phone": {"order"},
        "order": {"chat_ready", "restart"},
    }
    if state not in {"name", "email", "phone", "order"}:
        return False

    target_value = ""
    if state == "name":
        target_value = normalize_customer_name(session.customer_name)
    elif state == "order":
        target_value = normalize_order_num(session.order_num)
    elif state == "email":
        target_value = normalize_customer_email(session.customer_email)
    elif state == "phone":
        target_value = normalize_lenovo_phone(session.customer_phone)

    if not target_value:
        return False

    selectors_by_state = {
        "name": [
            "input[aria-label*=\"what's your name\" i]",
            "input[aria-label*='(1 of 4)' i]",
            "#insideWorkflowFieldCell input",
            "input[aria-label*='name' i]",
            "input[placeholder*='name' i]",
            "#insideWorkflowFieldCell textarea",
        ],
        "email": [
            "input[aria-label*='email' i]",
            "#insideWorkflowFieldCell input[type='email']",
            "#insideWorkflowFieldCell input",
            "input[placeholder*='email' i]",
        ],
        "phone": [
            "input[aria-label='xxx-xxx-xxxx']",
            "#insideWorkflowFieldCell input[type='tel']",
            "input[aria-label*='phone' i]",
            "#insideWorkflowFieldCell input",
            "input[placeholder*='phone' i]",
        ],
        "order": [
            "input[aria-label*='order number' i]",
            "#insideWorkflowFieldCell input",
            "input[aria-label*='order' i]",
            "input[placeholder*='order' i]",
        ],
    }

    step_markers = {
        "name": "what's your name",
        "email": "email address",
        "phone": "phone number",
        "order": "order number",
    }

    visible_candidate = None
    hidden_candidate = None

    def control_matches(target_state, meta):
        element_id = (meta.get("id") or "").strip().lower()
        element_type = (meta.get("type") or "").strip().lower()
        aria_label = (meta.get("aria") or "").strip().lower()
        placeholder = (meta.get("placeholder") or "").strip().lower()
        name_attr = (meta.get("name") or "").strip().lower()
        hay = " ".join(part for part in [element_id, element_type, aria_label, placeholder, name_attr] if part)
        if element_id == "chatinput" or element_type == "file":
            return False
        if target_state == "name":
            return "what's your name" in hay or "(1 of 4)" in hay or re.search(r"\bname\b", hay)
        if target_state == "email":
            return "email" in hay or element_id == "emailinput" or element_type == "email"
        if target_state == "phone":
            return "phone" in hay or "xxx-xxx-xxxx" in hay or element_type == "tel"
        if target_state == "order":
            return "order" in hay
        return False

    require_step_marker = forced_state is None

    for frame in frames:
        try:
            controls = await frame.query_selector_all("input, textarea")
        except Exception:
            controls = []
        for el in controls:
            try:
                meta = {
                    "id": await el.get_attribute("id") or "",
                    "type": await el.get_attribute("type") or "",
                    "name": await el.get_attribute("name") or "",
                    "aria": await el.get_attribute("aria-label") or "",
                    "placeholder": await el.get_attribute("placeholder") or "",
                }
                if not control_matches(state, meta):
                    continue
                if await el.is_visible():
                    visible_candidate = (frame, el, f"scan:{meta}")
                    break
                if not hidden_candidate:
                    hidden_candidate = (frame, el, f"scan:{meta}")
            except Exception:
                continue
        if visible_candidate:
            break

        try:
            txt = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.lower() : ''")
        except Exception:
            continue
        if require_step_marker and step_markers[state] not in txt:
            continue

        for sel in selectors_by_state.get(state, []):
            try:
                el = await frame.query_selector(sel)
                if not el:
                    continue
                element_id = (await el.get_attribute("id") or "").strip().lower()
                aria_label = (await el.get_attribute("aria-label") or "").strip().lower()
                placeholder = (await el.get_attribute("placeholder") or "").strip().lower()
                if element_id == "chatinput":
                    continue
                if state == "name" and "what's your name" not in f"{aria_label} {placeholder}" and "(1 of 4)" not in f"{aria_label} {placeholder}" and "insideworkflowfieldcell" not in sel.lower():
                    continue
                if state == "email" and "email" not in f"{aria_label} {placeholder}" and "insideworkflowfieldcell" not in sel.lower():
                    continue
                if state == "phone" and "phone" not in f"{aria_label} {placeholder}" and "xxx-xxx-xxxx" not in aria_label and "insideworkflowfieldcell" not in sel.lower():
                    continue
                if state == "order" and "order" not in f"{aria_label} {placeholder}" and "insideworkflowfieldcell" not in sel.lower():
                    continue
                if await el.is_visible():
                    visible_candidate = (frame, el, sel)
                    break
                if not hidden_candidate:
                    hidden_candidate = (frame, el, sel)
            except Exception:
                continue
        if visible_candidate:
            break

    if visible_candidate:
        frame, el, sel = visible_candidate
        print(f"  ℹ️  Lenovo advisor visible candidate: {state} via {sel}")
        try:
            await el.click(force=True)
        except Exception:
            pass
        try:
            await el.fill("")
        except Exception:
            pass
        try:
            await el.type(target_value, delay=35)
        except Exception:
            try:
                await el.fill(target_value)
            except Exception:
                return False
        try:
            current_val = await el.input_value()
            if (current_val or "").strip() != target_value.strip():
                print(f"  ℹ️  Lenovo advisor retry fill: {state} current='{current_val}' target='{target_value}'")
                try:
                    await el.fill(target_value)
                except Exception:
                    pass
        except Exception:
            pass
        print(f"  ✅ Lenovo step: {state} -> {target_value}")
        await page.wait_for_timeout(250)
        try:
            await el.press("Enter")
        except Exception:
            try:
                await frame.evaluate(
                    """
                    (selector) => {
                      const el = document.querySelector(selector) || document.activeElement;
                      if (!el) return false;
                      if (typeof el.focus === "function") el.focus();
                      ["keydown", "keypress", "keyup"].forEach((type) => {
                        el.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
                      });
                      return true;
                    }
                    """,
                    sel,
                )
            except Exception:
                return False
    elif hidden_candidate:
        frame, el, sel = hidden_candidate
        print(f"  ℹ️  Lenovo advisor hidden candidate: {state} via {sel}")
        try:
            await el.evaluate(
                """
                (el, value) => {
                  if (typeof el.focus === "function") el.focus();
                  el.value = "";
                  el.dispatchEvent(new Event("input", { bubbles: true }));
                  el.value = value;
                  el.dispatchEvent(new InputEvent("input", { bubbles: true, data: value }));
                  el.dispatchEvent(new Event("change", { bubbles: true }));
                  ["keydown", "keypress", "keyup"].forEach((type) => {
                    el.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
                  });
                  return true;
                }
                """,
                target_value,
            )
            print(f"  ✅ Lenovo step: {state} -> {target_value}")
        except Exception:
            return False
    else:
        print(f"  ℹ️  Lenovo advisor no candidate for state={state}")
        for idx, frame in enumerate(frames):
            try:
                controls = await frame.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('input, textarea'))
                      .slice(0, 12)
                      .map((el) => ({
                        tag: el.tagName.toLowerCase(),
                        id: el.id || '',
                        type: el.getAttribute('type') || '',
                        name: el.getAttribute('name') || '',
                        aria: el.getAttribute('aria-label') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        classes: el.className || '',
                        display: getComputedStyle(el).display,
                        visibility: getComputedStyle(el).visibility,
                      }))
                    """,
                )
                if controls:
                    print(f"  ℹ️  Lenovo advisor controls frame#{idx}: {controls[:6]}")
            except Exception:
                continue
        return False

    for _ in range(16):
        try:
            frame_text = await frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.toLowerCase() : ''")
        except Exception:
            frame_text = ""
        if state == "name" and ("email address" in frame_text or "correct email format" in frame_text):
            return True
        if state == "email" and "phone number" in frame_text:
            return True
        if state == "phone" and "order number" in frame_text:
            return True
        if state == "order" and "how can we help you today?" in frame_text:
            return True
        next_state = classify_lenovo_widget_state(await get_lenovo_widget_text(page))
        if next_state in expected_next.get(state, set()):
            return True
        await page.wait_for_timeout(200)
    return False

async def detect_lenovo_visible_state(page):
    """
    Определяет активный Lenovo step по реально видимому DOM,
    а не только по transcript text.
    """
    frames = [page.main_frame] + [fr for fr in page.frames if fr != page.main_frame]
    checks = [
        ("chat_ready", [
            "#insideWorkflowFieldCell input[aria-label*='how can we help you today' i]",
            "#insideWorkflowFieldCell textarea[aria-label*='how can we help you today' i]",
            "input[aria-label*='how can we help you today' i]",
            "textarea[aria-label*='how can we help you today' i]",
        ]),
        ("name", [
            "input[aria-label*=\"what's your name\" i]",
            "input[aria-label*='(1 of 4)' i]",
            "#insideWorkflowFieldCell input[aria-label*='name' i]",
        ]),
        ("email", [
            "input[aria-label*='email' i]",
            "#insideWorkflowFieldCell input[type='email']",
        ]),
        ("phone", [
            "input[aria-label='xxx-xxx-xxxx']",
            "#insideWorkflowFieldCell input[type='tel']",
            "input[aria-label*='phone' i]",
        ]),
        ("order", [
            "input[aria-label*='order number' i]",
            "#insideWorkflowFieldCell input",
        ]),
    ]
    for frame in frames:
        for state, selectors in checks:
            for sel in selectors:
                try:
                    el = await frame.query_selector(sel)
                    if el and await el.is_visible():
                        return state
                except Exception:
                    continue
        try:
            options = await frame.evaluate(
                """
                () => {
                  const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 8 && r.height > 8 && st.visibility !== "hidden" && st.display !== "none";
                  };
                  return Array.from(document.querySelectorAll(".picklistOption"))
                    .filter(visible)
                    .map(el => ((el.innerText || el.getAttribute("aria-label") || "").replace(/\\s+/g, " ").trim().toLowerCase()))
                    .filter(Boolean);
                }
                """
            )
        except Exception:
            options = []
        if not options:
            continue
        if "chat with an agent" in options:
            return "agent_entry"
        if "existing orders" in options or "existing order" in options:
            return "existing_pick"
        if "general question" in options:
            return "general_pick"
        if "operator" in options or "speak with an operator" in options:
            return "operator_pick"
        if "consumer" in options:
            return "consumer_pick"
    return None

async def advance_lenovo_widget_state(page, session, forced_state=None):
    """
    Читает текущий prompt Lenovo widget и выбирает следующее действие.
    """
    widget_text = await get_lenovo_widget_text(page)
    state = forced_state or await detect_lenovo_visible_state(page) or classify_lenovo_widget_state(widget_text)

    if state == "restart":
        return await restart_expired_lenovo_chat(page)
    if state == "agent_entry":
        if await click_lenovo_picklist_option(page, ["Chat with an Agent"]):
            print("  ✅ Lenovo step: Chat with an Agent")
            return True
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
    if state == "unknown":
        for fallback_state in ("name", "email", "phone", "order"):
            if await fill_lenovo_advisor_step(page, session, forced_state=fallback_state):
                print(f"  ℹ️  Lenovo fallback step: {fallback_state}")
                return True
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
        "chat_ready": bool(operator_open and state == "chat_ready"),
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
        state = await detect_lenovo_visible_state(page) or classify_lenovo_widget_state(widget_text)
        if state not in {"agent_entry", "existing_pick", "general_pick", "operator_pick", "consumer_pick", "name", "email", "phone", "order", "restart"}:
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
        if not getattr(session, "lenovo_widget_reset_done", False):
            try:
                current_widget_text = await get_lenovo_widget_text(page)
                current_widget_state = classify_lenovo_widget_state(current_widget_text)
            except Exception:
                current_widget_text = ""
                current_widget_state = "unknown"
            if current_widget_state in {"name", "email", "phone", "order", "chat_ready", "restart"}:
                if await reset_lenovo_widget(page):
                    session.lenovo_widget_reset_done = True
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
            widget_text = await get_lenovo_widget_text(page)
            visible_state = await detect_lenovo_visible_state(page) or classify_lenovo_widget_state(widget_text)
            print(f"  ✅ Lenovo step: Widget already open ({visible_state})")
            if visible_state in {
                "agent_entry",
                "existing_pick",
                "general_pick",
                "operator_pick",
                "consumer_pick",
                "name",
                "email",
                "phone",
                "order",
                "restart",
            }:
                if await advance_lenovo_widget_state(page, session, forced_state=visible_state):
                    await safe_wait(350)
                    if await is_operator_chat_open(page, store):
                        return
            if visible_state == "unknown" and await click_lenovo_contact_chat_cta(page):
                print("  ℹ️  Lenovo shell open without active step; re-clicked CTA")
                await safe_wait(500)
            if progressed and await is_operator_chat_open(page, store):
                return
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
            widget_state = classify_lenovo_widget_state(widget_text)
            if widget_state == "chat_ready":
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
        widget_state = await detect_lenovo_visible_state(page) or classify_lenovo_widget_state(widget_text)
        if widget_state == "chat_ready":
            return True
        # Lenovo иногда держит stale transcript text (`order`) после полного handoff.
        # Если live chat input уже виден вместе с финальным prompt/input, считаем чат готовым.
        for frame in frames:
            try:
                live_input = await frame.query_selector(
                    "textarea#chatInput:visible, textarea[placeholder='Type your message here']:visible, .cx-input textarea:visible"
                )
                final_prompt = await frame.query_selector(
                    "input[aria-label*='how can we help you today' i]:visible, textarea[aria-label*='how can we help you today' i]:visible"
                )
                if live_input and final_prompt:
                    return True
            except Exception:
                continue
        if widget_state in {"agent_entry", "existing_pick", "general_pick", "operator_pick", "consumer_pick", "name", "email", "phone", "order", "restart"}:
            for frame in frames:
                try:
                    live_input = await frame.query_selector(
                        "textarea#chatInput:visible, textarea[placeholder='Type your message here']:visible, .cx-input textarea:visible"
                    )
                    if not live_input:
                        continue
                    final_prompt = await frame.query_selector(
                        "input[aria-label*='how can we help you today' i]:visible, textarea[aria-label*='how can we help you today' i]:visible"
                    )
                    if final_prompt:
                        return True
                except Exception:
                    continue
            return False
        for frame in frames:
            try:
                has_widget = await frame.query_selector(
                    "[class*='cx-widget'], [id*='cx-container'], .cx-webchat, [class*='webchat' i], [class*='genesys' i], #insideChatPane, #inside_holder"
                )
                has_chat_input = await frame.query_selector(
                    "textarea[placeholder='Type your message here'], #chatInput, .cx-input textarea, [class*='cx-input'] textarea, [contenteditable='true'], #insideWorkflowFieldCell input[aria-label*='how can we help you today' i], #insideWorkflowFieldCell textarea[aria-label*='how can we help you today' i]"
                )
                if has_widget and has_chat_input and widget_state == "chat_ready":
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
        "regulation z",
        "billing-dispute",
        "billing dispute",
        "card issuer",
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
            session._append_transcript_entry("customer_rep", first_msg)
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
        audit_reason = "session_complete"
        should_exit_session = False

        def resume_pending_operator_request():
            requested_field = (session.pending_requested_field or "").strip()
            if not requested_field or not session.last_agent_msg:
                return None
            if not session._has_requested_field(requested_field):
                return None
            field_label = session._requested_field_label(requested_field)
            session.pending_requested_field = ""
            session._persist_case_memory()
            print(f"🟢 Получены новые данные ({field_label}) через UI. Продолжаю ответ на последний запрос оператора.")
            return session.last_agent_msg

        while True:
            session.consume_ui_commands()
            agent_msg = resume_pending_operator_request()
            agent_already_recorded = bool(agent_msg)

            if use_auto:
                if not agent_msg:
                    print("  👁  Слежу за чатом... (q = выход, m = ввести ответ агента вручную)")
                ticks = 0
                while not agent_msg:
                    await asyncio.sleep(1)
                    ticks += 1

                    session.consume_ui_commands()
                    agent_msg = resume_pending_operator_request()
                    agent_already_recorded = bool(agent_msg)
                    if agent_msg:
                        break

                    cmd = read_console_command()
                    if cmd:
                        cmd_low = cmd.lower()
                        if cmd_low in {"q", "quit", "exit"}:
                            audit_reason = "manual_quit"
                            should_exit_session = True
                            break
                        if cmd_low == "m":
                            manual = input("  Вставь ответ агента вручную: ").strip()
                            if manual:
                                agent_msg = manual
                                agent_already_recorded = False
                                break

                    if ticks % 5 == 0:
                        synced_customer = await read_last_customer_message(page, store)
                        if synced_customer and synced_customer != session.last_sent_msg:
                            session.record_customer_message(synced_customer, source="transcript_sync")
                        detected = await read_last_agent_message(page, store)
                        if detected and detected != session.last_agent_msg:
                            agent_msg = detected
                            session.last_agent_msg = detected
                            agent_already_recorded = False
                            print(f"\n💬 АГЕНТ: {agent_msg}")
            else:
                if not agent_msg:
                    agent_msg = input("\n  Вставь ответ агента: ").strip()
                    agent_already_recorded = False
                    if agent_msg.lower() == "q":
                        audit_reason = "manual_quit"
                        break

            if should_exit_session:
                break

            if not agent_msg:
                continue

            print("\n⚖️  Генерирую ответ...")
            session.consume_ui_commands()
            synced_customer = await read_last_customer_message(page, store)
            if synced_customer and synced_customer != session.last_sent_msg:
                session.record_customer_message(synced_customer, source="transcript_sync")
            requested_field = session._requested_customer_field(agent_msg)
            if requested_field and not session._has_requested_field(requested_field):
                if not agent_already_recorded:
                    session.record_agent_message(agent_msg)
                    agent_already_recorded = True
                session.pending_requested_field = requested_field
                session._persist_case_memory()
                print(
                    f"🛑 Оператор запросил {session._requested_field_label(requested_field)}, "
                    "но этих данных нет. Нажмите «Обновить данные кейса» в UI, и бот продолжит без перезапуска."
                )
                continue
            observation = await collect_chat_observation(page, store, session)
            plan = session.plan_next_action(
                agent_text=agent_msg,
                observation=observation,
                first_turn=False,
            )
            if not agent_already_recorded:
                session.record_agent_message(agent_msg)
                agent_already_recorded = True
            print(f"🧠 Agent action: {plan['action']} ({plan['reason'] or 'no reason'})")
            if plan["action"] == "finish":
                audit_reason = "planner_finish"
                print("🏁 Агентный контур завершил кейс.")
                break
            if plan["action"] == "wait":
                print("⏳ Агент решил подождать следующий шаг/ответ без отправки сообщения.")
                continue
            our_reply = (plan.get("message") or "").strip()
            if not our_reply:
                our_reply = session.generate_reply(agent_msg, already_recorded=agent_already_recorded)
            else:
                session.history.append({
                    "role": "user",
                    "content": f'Agent replied: "{agent_msg}"\nObservation: {json.dumps(observation, ensure_ascii=True)}',
                })
                session.history.append({"role": "assistant", "content": our_reply})
                session._append_transcript_entry("customer_rep", our_reply)
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
                if force_auto_mode or (auto_send_noncritical and auto_send_critical):
                    print("🤖 Авто-режим: продолжаю диалог без ручной паузы.")
                else:
                    cont = input("  Продолжить диалог? [y/N]: ").strip().lower()
                    if cont != "y":
                        audit_reason = "manual_stop_after_final"
                        break

        audit_path = session.generate_post_chat_audit(reason=audit_reason)
        if audit_path:
            print(f"🧾 Post-chat анализ сохранён: {audit_path}")
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
    run_config = load_run_config()
    if run_config:
        print("🗂️  Загружен конфиг запуска из интерфейса.")

    global OPENAI_API_KEY, OPENAI_MODEL, DOLPHIN_SESSION_TOKEN, DOLPHIN_CLOUD_API_KEY
    if not OPENAI_API_KEY:
        key = run_cfg_str(run_config, "openai_api_key") or ask("OpenAI API Key (sk-...)")
        os.environ["OPENAI_API_KEY"] = key
        OPENAI_API_KEY = key

    if not OPENAI_MODEL:
        model = run_cfg_str(run_config, "openai_model", "gpt-4.1-mini") or ask("OpenAI model", "gpt-4.1-mini")
        OPENAI_MODEL = model

    if not DOLPHIN_SESSION_TOKEN:
        token = run_cfg_str(run_config, "dolphin_session_token") if run_cfg_has(run_config, "dolphin_session_token") else ask("Dolphin Session Token (если требуется, иначе Enter)", "")
        if token:
            os.environ["DOLPHIN_SESSION_TOKEN"] = token
            DOLPHIN_SESSION_TOKEN = token

    if not DOLPHIN_CLOUD_API_KEY:
        cloud_key = run_cfg_str(run_config, "dolphin_cloud_api_key") if run_cfg_has(run_config, "dolphin_cloud_api_key") else ask("Dolphin{cloud} API-ключ (если есть, иначе Enter)", "")
        if cloud_key:
            os.environ["DOLPHIN_CLOUD_API_KEY"] = cloud_key
            DOLPHIN_CLOUD_API_KEY = cloud_key

    # ── Ввод имени профиля ──────────────────────────────────────────────────
    print()
    configured_autopilot = run_cfg_bool(run_config, "autopilot")
    autopilot = configured_autopilot if configured_autopilot is not None else ask("Режим автопилота (минимум вопросов)? [Y/n]", "y").lower() != "n"

    profile_name = run_cfg_str(run_config, "profile_name") or ask("Введи название профиля Dolphin Anty")
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

    configured_store = run_cfg_str(run_config, "store")
    configured_case_type = run_cfg_str(run_config, "case_type")
    configured_order_num = run_cfg_str(run_config, "order_num")
    configured_amount = run_cfg_str(run_config, "amount")
    configured_details = run_cfg_str(run_config, "details")
    configured_customer_name = run_cfg_str(run_config, "customer_name")
    configured_customer_email = run_cfg_str(run_config, "customer_email")
    configured_customer_phone = run_cfg_str(run_config, "customer_phone")

    if configured_store:
        store = configured_store
        print(f"  🏪 Магазин: {store}  (из конфига)")
    elif auto_store:
        print(f"  🏪 Магазин: {auto_store}  (из имени профиля)")
        store = auto_store
    else:
        store = "Lenovo.com" if autopilot else ask("Магазин (Amazon / Lenovo.com / Zara.com / другой)", "Lenovo.com")

    if configured_case_type:
        case_type = configured_case_type.upper()
        print(f"  📂 Тип кейса: {case_type}  (из конфига)")
    elif auto_case:
        print(f"  📂 Тип кейса: {auto_case}  (из имени профиля)")
        case_type = auto_case
    else:
        case_type = ("INR" if autopilot else ask("Тип [INR = не получил товар / RNR = не вернули деньги]", "INR")).upper()

    has_configured_customer_data = any([
        configured_order_num,
        configured_customer_name,
        configured_customer_email,
        configured_customer_phone,
    ])
    configured_use_block = run_cfg_bool(run_config, "use_block")
    if has_configured_customer_data:
        use_block = False
    elif configured_use_block is not None:
        use_block = configured_use_block
    else:
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
    elif has_configured_customer_data:
        block_data = {
            "name": configured_customer_name,
            "order": configured_order_num,
            "email": configured_customer_email,
            "phone": configured_customer_phone,
        }

    order_num = configured_order_num if run_cfg_has(run_config, "order_num") else (block_data.get("order") or ask("Номер заказа (Enter = пропустить)", ""))
    amount = configured_amount if run_cfg_has(run_config, "amount") else ask("Сумма в $ (Enter = пропустить)", "")
    details = configured_details if run_cfg_has(run_config, "details") else (ask("Детали проблемы", "") if not autopilot else "")
    details_lower = (details or "").lower()
    if any(k in details_lower for k in ["broken screen", "broken", "defective", "damaged", "replacement", "returned back", "returned to lenovo", "ups label"]):
        case_type = "DOA"
    customer_name = (configured_customer_name or client_name) if run_cfg_has(run_config, "customer_name") else (block_data.get("name") or ask("Имя клиента для pre-chat (Enter = клиент из профиля)", client_name))
    customer_email = configured_customer_email if run_cfg_has(run_config, "customer_email") else (block_data.get("email") or ask("Email для pre-chat (Enter = пропустить)", ""))
    customer_phone = configured_customer_phone if run_cfg_has(run_config, "customer_phone") else (block_data.get("phone") or ask("Phone для pre-chat (Enter = пропустить)", ""))
    if DEFAULT_CUSTOMER_NAME and customer_name == client_name:
        customer_name = DEFAULT_CUSTOMER_NAME
    customer_email = normalize_customer_email(customer_email or DEFAULT_CUSTOMER_EMAIL)
    customer_phone = normalize_customer_phone(customer_phone or DEFAULT_CUSTOMER_PHONE)
    order_num = normalize_order_num(order_num or DEFAULT_ORDER_NUM)
    if customer_phone and len(re.sub(r"\D+", "", customer_phone)) < 7:
        customer_phone = ""
    configured_prechat_only = run_cfg_bool(run_config, "prechat_only")
    prechat_only = configured_prechat_only if configured_prechat_only is not None else ask("Режим pre-chat only (без онлайн-диалога)? [y/N]", "n").lower() == "y"

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
            configured_auto_send_noncritical = run_cfg_bool(run_config, "auto_send_noncritical")
            configured_auto_send_critical = run_cfg_bool(run_config, "auto_send_critical")
            configured_force_auto_mode = run_cfg_bool(run_config, "force_auto_mode")
            auto_send_noncritical = configured_auto_send_noncritical if configured_auto_send_noncritical is not None else ask("Авто-отправка не критичных шагов? [y/N]", "n").lower() == "y"
            auto_send_critical = configured_auto_send_critical if configured_auto_send_critical is not None else ask("Авто-отправка критичных шагов? [y/N]", "n").lower() == "y"
            force_auto_mode = configured_force_auto_mode if configured_force_auto_mode is not None else False

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
