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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DOLPHIN_SESSION_TOKEN = os.getenv("DOLPHIN_SESSION_TOKEN", "")
DOLPHIN_CLOUD_API_KEY = os.getenv("DOLPHIN_CLOUD_API_KEY", "")

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

QUALITY RULES:
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
- Avoids emotional or vague language."""

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
        "messages": ".cx-message, [class*='cx-message'], .cx-bubble",
        # Агентские сообщения — имеют класс cx-agent или просто не имеют cx-visitor
        "agent_msg": ".cx-message:not(.cx-visitor), [class*='cx-agent-message'], .cx-bubble-agent",
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
        # Тогда делаем stop и повторяем старт в automation-режиме.
        err = str(data.get("error", "")).lower()
        if "already running" in err:
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
    def __init__(self, store, case_type, order_num="", amount="", details="", customer_name="", customer_email=""):
        self.store = store
        self.case_type = case_type
        self.order_num = order_num
        self.amount = amount
        self.details = details
        self.customer_name = customer_name
        self.customer_email = customer_email
        self.history = []
        self.message_count = 0
        self.last_agent_msg = ""

    def generate_first_message(self):
        prompt = f"""Start a live support chat with {self.store}.
Case: {"Item Not Received" if self.case_type == "INR" else "Return Not Refunded"}
Order: {self.order_num or "N/A"}
Amount: {f"${self.amount}" if self.amount else "N/A"}
Details: {self.details or "none"}
Write the first message to the support agent."""

        self.history = [{"role": "user", "content": prompt}]
        reply = self._call_llm()
        self.history.append({"role": "assistant", "content": reply})
        self.message_count = 1
        return reply

    def generate_reply(self, agent_text):
        n = self.message_count
        escalation = (
            "Step 2: be firm; ask for supervisor/escalations team and a case ID with timeline." if n == 1 else
            "Step 3: if denial/stalling, request written policy basis; mention FCBA/FTC and dispute rights." if n == 2 else
            "Final step: set a final deadline and request immediate resolution confirmation in writing."
        )
        prompt = f"""Agent replied: "{agent_text}"
{escalation}
Write the next message that maximizes chance of a positive resolution now.
Preferred outcomes priority: full refund > free replacement > supervisor escalation with case ID and deadline."""
        self.history.append({"role": "user", "content": prompt})
        reply = self._call_llm()
        self.history.append({"role": "assistant", "content": reply})
        self.message_count += 1
        return reply

    def _call_llm(self):
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            # Необязательные заголовки OpenRouter для идентификации приложения
            "HTTP-Referer": "http://localhost",
            "X-Title": "Support Copilot Dolphin Bot",
        }
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + self.history,
            "max_tokens": 700,
            "temperature": 0.35,
        }

        r = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=60)
        data = r.json()
        if r.status_code >= 400:
            err = data.get("error", {}).get("message") or data
            raise RuntimeError(f"OpenRouter API error: {err}")

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenRouter API returned empty choices: {data}")
        return (choices[0].get("message", {}).get("content") or "").strip()


# ─── CHAT READER/WRITER ──────────────────────────────────────────────────────

def get_store_name(url):
    for key in CHAT_SELECTORS:
        if key in url and key != "default":
            return key
    return "default"

async def read_last_agent_message(page, store):
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        # Сначала пробуем точный селектор агентских сообщений
        agent_sel = sel.get("agent_msg", sel.get("messages", ""))
        elements = await page.query_selector_all(agent_sel)
        agent_msgs = []
        for el in elements:
            text = await el.inner_text()
            text = text.strip()
            # Пропускаем системные сообщения типа "One moment please..."
            if not text or len(text) < 5:
                continue
            skip_phrases = ["one moment", "transfer", "connecting", "please wait", "queue"]
            if any(p in text.lower() for p in skip_phrases):
                continue
            agent_msgs.append(text)

        if agent_msgs:
            return agent_msgs[-1]

        # Fallback: читаем все сообщения и фильтруем по классу
        all_elements = await page.query_selector_all(sel["messages"])
        for el in reversed(all_elements):
            text = (await el.inner_text()).strip()
            if not text or len(text) < 5:
                continue
            cls = (await el.get_attribute("class") or "").lower()
            is_ours = any(w in cls for w in ["visitor", "customer", "user", "outgoing", "sent"])
            if not is_ours:
                return text
        return None
    except:
        return None

async def type_message(page, store, text):
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        input_el = await page.query_selector(sel["input"])
        if input_el:
            await input_el.click()
            await input_el.fill("")
            await input_el.type(text, delay=30)  # человекоподобный ввод
            return True
    except Exception as e:
        print(f"  ⚠️  Не удалось вставить текст: {e}")
    return False

async def send_message(page, store):
    sel = CHAT_SELECTORS.get(store, CHAT_SELECTORS["default"])
    try:
        btn = await page.query_selector(sel["send"])
        if btn:
            await btn.click()
            return True
        # Fallback: Enter
        input_el = await page.query_selector(sel["input"])
        if input_el:
            await input_el.press("Enter")
            return True
    except:
        pass
    return False

async def click_first_visible(page, selectors):
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.click()
                return True
        except Exception:
            continue
    return False

async def fill_first_input(page, selectors, value):
    if not value:
        return False
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.click()
                await el.fill(value)
                return True
        except Exception:
            continue
    return False

async def prepare_chat_for_operator(page, store, session):
    """Автоподготовка pre-chat: выбор раздела и заполнение полей перед подключением оператора."""
    print("🧭 Подготавливаю чат перед подключением оператора...")

    try:
        await page.wait_for_timeout(1200)
    except Exception:
        pass

    # Попытка открыть/развернуть виджет чата
    await click_first_visible(page, [
        "button:has-text('Chat')",
        "button:has-text('Live Chat')",
        "button:has-text('Need help')",
        "button:has-text('Contact us')",
        "[aria-label*='chat' i]",
        "[class*='chat' i] button",
    ])
    await page.wait_for_timeout(800)

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
        await page.wait_for_timeout(700)

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

    # Переход к оператору/следующему шагу
    clicked_continue = await click_first_visible(page, [
        "button:has-text('Continue')",
        "button:has-text('Start chat')",
        "button:has-text('Start Chat')",
        "button:has-text('Connect')",
        "button:has-text('Submit')",
        "button[type='submit']",
    ])

    if filled_order or filled_name or filled_email or clicked_continue:
        print("✅ Pre-chat шаги выполнены (где элементы были найдены).")
    else:
        print("ℹ️  Pre-chat элементы не обнаружены, продолжаю в обычном режиме.")

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
        page = pages[0] if pages else await context.new_page()

        url = page.url
        store = get_store_name(url)
        print(f"🌐 Страница: {url}")
        print(f"🏪 Магазин: {store}\n")
        await prepare_chat_for_operator(page, store, session)

        # Генерируем первое сообщение
        print("⚖️  Генерирую первое сообщение...")
        first_msg = session.generate_first_message()

        print("\n" + "─" * 55)
        print("📤 ПЕРВОЕ СООБЩЕНИЕ (для отправки агенту):")
        print("─" * 55)
        print(first_msg)
        print("─" * 55)

        # Вставляем в чат
        typed = await type_message(page, store, first_msg)
        if typed:
            print("✅ Текст вставлен в поле чата")
        else:
            print("⚠️  Не удалось вставить автоматически — скопируй вручную")

        first_is_critical = is_critical_message(first_msg, session.message_count)
        should_confirm_first = (first_is_critical and not auto_send_critical) or (
            not first_is_critical and not auto_send_noncritical
        )
        if should_confirm_first:
            confirm = input("\n  Отправить сообщение? [Y/n]: ").strip().lower()
            allow_send = confirm != "n"
        else:
            first_mode = "критичный шаг" if first_is_critical else "не критичный шаг"
            print(f"🤖 Авто-отправка: {first_mode}, отправляю без подтверждения.")
            allow_send = True

        if allow_send:
            sent = await send_message(page, store)
            print("✅ Отправлено!" if sent else "⚠️  Нажми Enter в чате вручную")

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
            our_reply = session.generate_reply(agent_msg)

            print("\n" + "─" * 55)
            step_labels = {1: "ЭСКАЛАЦИЯ 2 — Требование", 2: "ЭСКАЛАЦИЯ 3 — Юридическое давление", 3: "ФИНАЛЬНОЕ ТРЕБОВАНИЕ"}
            label = step_labels.get(session.message_count - 1, f"СООБЩЕНИЕ {session.message_count}")
            print(f"📤 {label}:")
            print("─" * 55)
            print(our_reply)
            print("─" * 55)

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
                sent = await send_message(page, store)
                print("✅ Отправлено!" if sent else "⚠️  Нажми Enter в чате вручную")

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
    if "inr" in name_lower or "not received" in name_lower or "не получил" in name_lower:
        return "INR"
    if "rnr" in name_lower or "refund" in name_lower or "возврат" in name_lower:
        return "RNR"
    return None


async def main():
    print_banner()

    global OPENROUTER_API_KEY, OPENROUTER_MODEL, DOLPHIN_SESSION_TOKEN, DOLPHIN_CLOUD_API_KEY
    if not OPENROUTER_API_KEY:
        key = ask("OpenRouter API Key (sk-or-...)")
        os.environ["OPENROUTER_API_KEY"] = key
        OPENROUTER_API_KEY = key

    if not OPENROUTER_MODEL:
        model = ask("OpenRouter model", "openrouter/free")
        OPENROUTER_MODEL = model

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
    public_started = start_profile_public_by_name(profile_name)
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
                print(f"✅ Профиль запущен через real profile_id из логов (порт {cdp_port})")
            else:
                real_id = None
        if not real_id:
            restarted = restart_running_profile_for_automation()
            if restarted:
                profile_id = restarted["profile_id"]
                cdp_port = restarted["port"]
                full_name = profile_name
                print(f"✅ Подключение через fallback: перезапущен запущенный профиль (порт {cdp_port})")
            else:
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

    order_num = ask("Номер заказа (Enter = пропустить)", "")
    amount = ask("Сумма в $ (Enter = пропустить)", "")
    details = ask("Детали проблемы", "") if not autopilot else ""
    customer_name = ask("Имя клиента для pre-chat (Enter = клиент из профиля)", client_name)
    customer_email = ask("Email для pre-chat (Enter = пропустить)", "")

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
        customer_name=customer_name, customer_email=customer_email
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
        )
    finally:
        stop_profile(profile_id)
        print("🔴 Профиль остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
