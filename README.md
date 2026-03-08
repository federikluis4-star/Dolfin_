# 🐬 Support Copilot — Dolphin Anty Bot

## Как это работает

```
Dolphin Anty (запущен)
    ↓ Local API порт 3001
bot.py подключается
    ↓ CDP (Chrome DevTools Protocol)
Читает DOM чата на странице
    ↓
Отправляет контекст в OpenAI API
    ↓
Строит следующий ход с учётом:
  - последнего сообщения оператора
  - памяти диалога
  - противоречий
  - текущей цели спора
    ↓
Вставляет ответ в поле чата
    ↓
Отправляет ответ с human-like задержкой
```

---

## ⚙️ Установка (один раз)

### 1. Установи Python 3.10+
Скачай с python.org если нет.

### 2. Установи зависимости
```bash
cd dolphin-bot
pip install -r requirements.txt
playwright install chromium
```

### 3. Настрой Dolphin Anty
- Открой Dolphin Anty
- Настройки → **Local API** → включить → порт **3001**
- Создай профили под нужные магазины

### 4. Получи API ключ OpenAI
- Зайди на https://platform.openai.com/
- API keys → Create new secret key → скопируй `sk-...`
- Сохрани ключ в `.env` (см. ниже)

---

## 🚀 Запуск

### Вариант A: через `.env` (рекомендуется)
```bash
cd dolphin-bot
python3 bot.py
```

Пример `.env`:
```bash
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_MODEL=gpt-4.1-mini
DOLPHIN_SESSION_TOKEN=
DOLPHIN_CLOUD_API_KEY=
```

### Вариант B: ввод при запуске
```bash
python bot.py
# Программа спросит ключ при старте
```

---

## 🧠 Что уже умеет агент

- Работает через `OpenAI API` как reasoning-слой, а не только шаблоны.
- Держит `stateful` память переговоров внутри сессии:
  - `operator_claims`
  - `confirmed_facts`
  - `unresolved_demands`
  - `contradictions`
  - `dialogue_state`
- Различает типы хода оператора:
  - `keepalive`
  - `UPS redirect`
  - `empty-box claim`
  - `warehouse not received`
  - `case ID provided`
  - `escalation confirmed`
  - `transcript offer`
- Проверяет ответ перед отправкой:
  - по теме ли он последней реплике,
  - не дублирует ли прошлый ход,
  - не звучит ли бот как сам продавец.
- Отвечает не мгновенно, а с короткой human-like задержкой.

---

## 📋 Процесс работы

1. Запусти `python bot.py`
2. Укажи профиль Dolphin Anty
3. Вставь данные кейса:
   - `name`
   - `email`
   - `phone`
   - `order`
   - при необходимости `details`
4. Бот сам:
   - поднимет профиль,
   - подключится по CDP,
   - откроет Lenovo chat,
   - пройдёт pre-chat flow,
   - доведёт чат до оператора
5. После ответа оператора бот:
   - читает последний ход,
   - определяет intent,
   - строит следующий шаг через OpenAI,
   - прогоняет critic-pass,
   - вставляет/отправляет ответ
6. Для чистой проверки логики лучше тестировать на новом чате, а не на уже загрязнённой переписке

---

## ⚠️ Важно

- Dolphin Anty должен быть **запущен** до старта бота
- Local API должен быть **включён** в настройках Dolphin Anty
- Lenovo — наиболее развитая интеграция, но и наиболее чувствительная к runtime-изменениям DOM/виджета
- Если старый чат уже загрязнён неудачными ответами, открывай новый чистый чат для следующего теста
- Если авто-вставка не работает на каком-то сайте — бот всё равно покажет текст, и его можно вставить вручную

---

## 🔧 Если что-то не работает

**"Dolphin Anty не запущен"**
→ Открой Dolphin Anty, включи Local API в настройках

**"Не удалось вставить текст"**
→ Сайт защищён от автоматизации. Скопируй текст из консоли и вставь вручную.

**"Профили не найдены"**
→ Проверь что Local API включён на порту 3001

**API ошибка OpenAI**
→ Проверь ключ `OPENAI_API_KEY`, доступ к API и биллинг в OpenAI

**Бот отвечает нелогично**
→ Обычно это значит, что тест идёт в уже загрязнённом старом чате или операторский transcript содержит старые дубли. Для проверки новой логики лучше открыть новый чистый чат.

---

## 🧾 Документация работ

Для каждого commit/push обязательно обновлять:
- [`docs/COMMIT_PUSH_POLICY.md`](docs/COMMIT_PUSH_POLICY.md)
- [`docs/PROJECT_CHRONOLOGY.md`](docs/PROJECT_CHRONOLOGY.md)
- [`docs/ERROR_LOG.md`](docs/ERROR_LOG.md) — если были ошибки/регрессии

Это обязательное правило проекта.

---

## 🛡️ Guardrails (обязательно)

В репозитории включены автоматические проверки:
- `AGENTS.md` — базовые правила для любого агента/разработчика.
- `.githooks/pre-commit` — блокирует commit кода без обновления `docs/PROJECT_CHRONOLOGY.md`.
- `.githooks/commit-msg` — для `fix/bug/error` требует обновить `docs/ERROR_LOG.md`.
- `.githooks/pre-push` — блокирует push, если в диапазоне push нет обязательных обновлений docs.

Локальная конфигурация уже выставлена:
- `core.hooksPath=.githooks`
- `commit.template=.gitmessage.txt`
