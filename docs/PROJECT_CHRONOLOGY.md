# Project Chronology

## Format
Use one entry per significant work block.

- Date (YYYY-MM-DD)
- Scope
- Actions
- Result
- Issues/Notes

---

## 2026-03-10
- Scope: Lenovo widget recovery and pre-chat workflow stabilization to first operator handoff.
- Actions:
  - Hardened `get_lenovo_widget_text()` to prefer visible active widget roots over hidden stale transcript containers.
  - Added explicit recovery handling for mixed Lenovo states such as expired panes, top-menu overlays, and `Chat with an Agent` entry screens.
  - Tightened `classify_lenovo_widget_state()` so mixed hidden/visible Lenovo render states route to the actionable branch (`existing_pick`, `agent_entry`, or `restart`) instead of looping on stale history.
  - Reworked Lenovo picklist clicking to prefer real `.picklistOption` elements over non-clickable ancestor containers with the same text.
  - Rewrote `fill_lenovo_advisor_step()` around the live-proven path: detect the active frame, target the correct workflow input, submit directly, and verify the next global widget state.
  - Updated the local runtime defaults for the active test case so the bot would not fall back to stale customer data while debugging.
- Result:
  - Live validation on profile `Katrin_NJ` reached the final prompt `Diana Bardian, how can we help you today?`, sent `Hello`, and received `One moment please while I transfer you to an Operator.`
- Issues/Notes:
  - This block established the live-probe path first; later the full normal runtime was separately validated end-to-end and documented below.

## 2026-03-10
- Scope: Lenovo full runtime success milestone.
- Actions:
  - Re-ran the normal `python3 bot.py` path on profile `Katrin_NJ` after switching Lenovo advisor filling from brittle selectors to live `input/textarea` scanning by `aria-label`, `id`, and `type`.
  - Confirmed the runtime itself, without a manual live-input intervention, completed:
    - `Existing Orders`
    - `General question`
    - `Operator`
    - `Consumer`
    - `name`
    - `email`
    - `phone`
    - `order`
    - first outgoing chat message
- Result:
  - Success fixed and confirmed: the bot now reaches Lenovo chat-ready state and opens the live chat on the normal runtime path by itself.
- Issues/Notes:
  - The remaining risk is no longer the advisor form itself; it is the quality of downstream dialogue logic for each case type.

## 2026-03-10
- Scope: Live dialogue review and operator-message quality control.
- Actions:
  - Added a persistent review trace pipeline to log:
    - operator message,
    - inferred intent,
    - draft reply,
    - critic verdict,
    - final sent message.
  - Added filtering for transient typing indicators such as `Advisor is typing` so the bot does not answer to non-message noise.
  - Kept the trace local in `logs/live_chat_review.jsonl` to support iterative tuning of reply quality without changing model weights.
- Result:
  - The bot can now be tuned from real operator transcripts with a concrete audit trail of why each answer was chosen and whether critic-pass changed it.
- Issues/Notes:
  - This improves supervision and correction quality; it does not replace the need for case-specific strategy rules when the merchant changes stance mid-dialogue.

## 2026-03-10
- Scope: Agent workflow policy hardening around sandbox approvals and live browser probes.
- Actions:
  - Updated `AGENTS.md` to explicitly prefer normal `python3 bot.py` runs and code-side instrumentation over ad-hoc escalated CDP probes.
  - Added repository-level guidance that the owner expects autonomous execution for ordinary run/edit/test work and that sandbox prompts are a tool-layer constraint, not a product workflow.
  - Updated `docs/AGENT_ENTRYPOINT.md` so future agents default to runtime logs first and use live browser probes only as a short last resort.
- Result:
  - Future work in this repository now has a documented policy to reduce approval friction and rely less on live escalated browser diagnostics.
- Issues/Notes:
  - This does not remove sandbox approval prompts enforced by the Codex environment itself; it reduces how often the project should need them.

## 2026-03-08
- Scope: Documentation sync with current OpenAI/stateful-agent architecture.
- Actions:
  - Updated `README.md` to describe the current OpenAI-based reasoning flow, stateful negotiation memory, human-like delays, and the recommended clean-chat testing workflow.
  - Updated `docs/AGENT_ENTRYPOINT.md` to match the current runtime architecture, including critic pass, intent classification, negotiation memory, and current operational caveats.
- Result:
  - Operator-facing and agent-facing documentation now reflects the actual bot behavior instead of the older manual-approval prototype description.
- Issues/Notes:
  - Repository dependency cleanup is still pending because legacy Anthropic-era files remain in the project metadata.

## 2026-03-08
- Scope: Stateful reasoning upgrade for operator negotiation.
- Actions:
  - Added persistent negotiation memory inside `CopilotSession`, including `operator_claims`, `confirmed_facts`, `unresolved_demands`, `contradictions`, and `dialogue_state`.
  - Added `agent_intent` and `current_objective` to the case snapshot so the decision layer can reason from the operator's latest move and the current dispute stage.
  - Added a critic pass that re-checks drafted replies and rejects outputs that do not address the latest operator move or drift back into generic escalation language.
  - Added contradiction tracking so conflicting Lenovo claims like `empty box` versus `warehouse did not receive the return` are remembered as structured state.
- Result:
  - The bot now has a more stateful negotiation layer and a stronger chance of producing logically consistent replies instead of repeating broad case-level demands.
- Issues/Notes:
  - This improves reasoning quality in code, but a fresh clean live chat is still needed to validate real operator handling after the previous polluted transcript.

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

## 2026-03-10
- Scope: Lenovo full runtime recovery through advisor form.
- Actions:
  - Reworked Lenovo advisor form filling to scan live `input/textarea` controls by `aria-label`, `id`, and `type` instead of relying only on brittle CSS selectors.
  - Tightened Lenovo widget-open handling so a visible shell without an active step re-clicks the CTA instead of looping forever on `Widget already open`.
  - Added visible-state handoff so an already-open widget executes the detected step (`name/email/phone/order`) immediately.
  - Validated the full `python3 bot.py` runtime on profile `Katrin_NJ` through:
    - `Existing Orders`
    - `General question`
    - `Operator`
    - `Consumer`
    - `name`
    - `email`
    - `phone`
    - `order`
    - first outgoing chat message
- Result:
  - The bot now reaches Lenovo chat-ready state on the normal runtime path and can self-complete the advisor form without a manual live probe.
- Issues/Notes:
  - Lenovo still mixes stale transcript text with the active prompt, so final operator-ready detection remains sensitive and should continue to be monitored.

## 2026-03-10
- Scope: First-person negotiation hardening for live Lenovo operator chats.
- Actions:
  - Rewrote the support-writing prompts so live chat replies are generated strictly as the account holder in first person, not in third person as `the customer`.
  - Added explicit intent handling for operator burden-shift rhetoric such as `return required before refund`, `retrieve and return again`, and soft stalling language.
  - Added reply sanitization and critic rejection for third-person wording and weak replies that fail to answer the operator's latest claim in the first sentence.
  - Removed the forced manual pause after multiple bot messages when full auto mode is enabled, so live chats no longer stall on `Продолжить диалог?`.
- Result:
  - The bot is better aligned with live operator rhetoric and no longer relies on third-person case phrasing in negotiation replies.
- Issues/Notes:
  - These improvements need live revalidation on the next clean operator exchange because Lenovo chat state can still be interrupted by widget resets or disconnects.
