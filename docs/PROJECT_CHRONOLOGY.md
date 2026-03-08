# Project Chronology

## Format
Use one entry per significant work block.

- Date (YYYY-MM-DD)
- Scope
- Actions
- Result
- Issues/Notes

---

## 2026-03-08
- Scope: Chat reasoning hardening for Lenovo operator dialogue.
- Actions:
  - Added explicit operator-intent handling for `keepalive`, `UPS redirect`, `empty-box claim`, `warehouse not received`, `case ID provided`, `escalation confirmed`, and `transcript offer`.
  - Extended the case snapshot with `last_agent_message`, `last_customer_message`, `agent_intent`, and `current_objective` so the agent loop reasons from the latest move instead of only the raw transcript tail.
  - Added reply safeguards that reject role-inverted merchant-style answers, reject replies that do not address the latest operator intent, and block near-duplicate outbound messages.
  - Added human-like send delay and improved Lenovo transcript reading so the bot no longer treats Powerfront system/menu text as real operator dialogue.
- Result:
  - The bot is less likely to repeat the same escalation wording, more likely to answer the operator's exact last move, and safer against duplicate or logically mismatched replies in Lenovo chats.
- Issues/Notes:
  - Live end-to-end operator validation is still required because the current live loop was interrupted and should be restarted from a clean chat session.

## 2026-03-08
- Scope: Agent-style dialogue controller and Lenovo workflow-input routing fix.
- Actions:
  - Added a structured OpenAI decision layer that returns `send_message`, `wait`, or `finish` from live case context, transcript tail, and UI observation.
  - Wired runtime observation into the first outbound message and each later reply so the bot reasons before acting instead of only templating a response.
  - Tightened Lenovo workflow-field filling so required advisor steps target workflow inputs and avoid the lower free-form `#chatInput`.
  - Hardened Lenovo runtime detection to avoid treating menu text such as `CHAT WITH US` and pre-chat prompts as real operator messages.
- Result:
  - The bot now has an explicit `observe -> reason -> act` loop for operator dialogue and no longer routes customer data into the wrong Genesys field during Lenovo form steps.
- Issues/Notes:
  - A full live regression run is still required to verify the new decision layer against the current Lenovo widget render and restored API billing.

## 2026-03-08
- Scope: Repository secret hygiene hardening.
- Actions:
  - Verified tracked files and git history for `.env`/API key leakage and found no committed real secrets.
  - Expanded `.gitignore` to exclude common key and certificate files in addition to `.env` files.
  - Strengthened `.githooks/pre-commit` and `.githooks/pre-push` to block staged or pushed diffs containing API keys, Dolphin tokens, or private key material.
- Result:
  - The repository now has stronger prevention against secret leakage to remote repositories.
- Issues/Notes:
  - `.env.example` remains tracked by design and currently contains placeholders only.

## 2026-03-07
- Scope: Lenovo insideChatFrame picklist compatibility fix.
- Actions:
  - Inspected the live Lenovo widget DOM over CDP and confirmed workflow steps are rendered inside `#insideChatFrame` as `.picklistOption` entries.
  - Added dedicated picklist-option clicking and widened Lenovo widget detection to include `insideChatFrame/insideChatPane` structures.
- Result:
  - Live validation confirmed the flow advances from `Existing Orders` to the next advisor step `"(1 of 4) What's your name?"`.
- Issues/Notes:
  - Further advisor steps still depend on runtime field matching and should be validated in a full end-to-end conversation.

## 2026-03-07
- Scope: Reduced Playwright click stalls in early Lenovo chat preparation.
- Actions:
  - Updated `click_first_visible` to use bounded `force=True` clicks with a short timeout and selector logging.
- Result:
  - Early chat-preparation clicks no longer wait on long actionability checks for partially covered Lenovo controls.
- Issues/Notes:
  - Live validation is still needed because Lenovo widget layering can change per render.

## 2026-03-07
- Scope: Lenovo launcher diagnostics and stronger fallback clicking.
- Actions:
  - Added runtime logging around `Chat Now` waiting and floating launcher discovery.
  - Strengthened floating launcher clicks with combined DOM click plus direct mouse click on the detected element center.
- Result:
  - Next live runs expose whether Lenovo fails on detection, click delivery, or post-click widget expansion.
- Issues/Notes:
  - This change improves observability first; runtime validation is still required against the live widget.

## 2026-03-07
- Scope: Lenovo chat launcher click reliability fix during live runtime validation.
- Actions:
  - Corrected launcher fallback candidate selection in `bot.py` so the rightmost/bottommost floating chat element is actually clicked.
  - Confirmed the failure mode during a live `Luna_CA` run where the blue launcher was visible but automation did not advance.
- Result:
  - Floating launcher fallback now targets the intended Lenovo chat button instead of the least relevant candidate in the sorted set.
- Issues/Notes:
  - Lenovo widget behavior remains timing-sensitive, so runtime validation after this fix is still required.

## 2026-03-07
- Scope: Agent onboarding entrypoint and repository operating context.
- Actions:
  - Added `docs/AGENT_ENTRYPOINT.md` as the mandatory first-read document for new agents/contributors.
  - Documented current architecture, technology stack, dirty working tree state, operating constraints, and expected start/end workflow.
  - Updated `AGENTS.md` so the repository guardrails explicitly point new contributors to the entrypoint before editing code.
- Result:
  - New threads can onboard faster against the real current project state instead of relying only on scattered docs or commit history.
- Issues/Notes:
  - The repo still has live uncommitted product changes in `bot.py`, `README.md`, `.env.example`, `install.sh`, and `requirements.txt`; future agents must inspect diffs before assuming `HEAD` is canonical.

## 2026-03-06
- Scope: Stabilization of Lenovo chat automation flow.
- Actions:
  - Added stronger chat opening logic (Lenovo `Chat Now` handling, retries, fallbacks).
  - Added stricter chat-target validation to avoid writing into non-chat fields.
  - Added Lenovo flow steps sequencing (`Existing Orders -> General question -> Operator -> Consumer`).
  - Added safer profile behavior controls to avoid unwanted stop/start cycles.
- Result:
  - Bot reaches chat flow more reliably and avoids several prior mis-targeting cases.
- Issues/Notes:
  - Lenovo widget behavior is dynamic and can vary by render timing.
  - Fully deterministic opening still depends on page state and runtime timing.

## 2026-03-06
- Scope: Repository-wide guardrails for any model/agent/human contributor.
- Actions:
  - Added mandatory repository policy file (`AGENTS.md`).
  - Added git hooks (`pre-commit`, `commit-msg`, `pre-push`) in `.githooks/`.
  - Added commit message template (`.gitmessage.txt`).
  - Added PR template (`.github/pull_request_template.md`).
  - Updated README with guardrails section and enforced workflow details.
  - Configured local repo hooks path and commit template.
- Result:
  - Any contributor/model is constrained by the same commit/push documentation rules.
- Issues/Notes:
  - Hook enforcement is local to repository clone; each clone must keep hooks enabled.

## 2026-03-05
- Scope: Environment/config and project baseline docs.
- Actions:
  - Added `.env` support and `.gitignore` protection for secrets.
  - Added `.env.example` and updated README for environment-based startup.
- Result:
  - Safer local configuration workflow established.
- Issues/Notes:
  - Remote `origin` may be missing in local repository setup.
