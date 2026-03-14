# Project Chronology

## Format
Use one entry per significant work block.

- Date (YYYY-MM-DD)
- Scope
- Actions
- Result
- Issues/Notes

---

## 2026-03-11
- Scope: Runtime bot hints and in-session case-data updates from the browser UI.
- Actions:
  - Added a runtime command queue shared between `web_ui.py`, `ui_runtime.py`, and `bot.py` so the live bot process can receive operator hints and case-data updates without a restart.
  - Added `Подсказка боту` and `Обновить данные кейса` controls in the browser panel, both using free-form multi-line blocks instead of separate columns.
  - Extended `bot.py` case memory with `operator_notes` and `pending_requested_field`, and taught the live loop to pause when the operator asks for missing `email/phone/order/name` until the user updates the case from the UI.
  - Switched UI-launched sessions to fully non-interactive live mode so browser-driven runs do not stall on hidden CLI confirmation prompts.
- Result:
  - During a live dialogue, the operator can now inject strategy notes or missing customer data into the running bot session, and the bot can resume the same operator question after the missing data is supplied.
- Issues/Notes:
  - Runtime commands are consumed by the active bot loop; if the process is not running, the UI correctly rejects the action instead of queuing it for later.

## 2026-03-11
- Scope: One-block new-case intake in the browser UI.
- Actions:
  - Added a `Новый кейс одним блоком` flow in `web_ui.py` so operators can paste `profile + customer data + order + problem` into one textarea instead of filling separate fields.
  - Added backend parsing for both labeled and simple ordered-line intake formats and converted that block directly into a `run_config` launch for `bot.py`.
  - Saved the `case -> Dolphin profile` binding from the intake flow when `store/order` are known so the case list keeps the same profile-aware title format later.
- Result:
  - New cases can now be launched from a single pasted block, while the bot still receives structured startup data and skips the old sequential questionnaire.
- Issues/Notes:
  - The one-block parser is heuristic by design; strongly structured input still gives the most predictable extraction.

## 2026-03-11
- Scope: Transcript-backed case memory and wait-timer correction.
- Actions:
  - Switched the case dashboard timers in `web_ui.py` from generic `updated_at` saves to dialogue-aware anchors: `last_event_at` for elapsed time and `follow_up_anchor_at` for follow-up deadlines.
  - Added transcript-backed repair logic in `bot.py` so follow-up anchors are re-derived from the saved agent transcript and stale case-memory timestamps do not shift promised wait windows.
  - Extended the dashboard copy to make it explicit that the bot resumes from the full saved transcript file, not just the short tail shown in the UI.
  - Cleaned the current Lenovo case memory/transcript from local test-only retry messages so the active case returned to the real stop point of `2026-03-11 00:43` America/Los_Angeles.
- Result:
  - The saved Lenovo case now shows `13` real transcript messages, the correct stop point, and a follow-up time of `2026-03-13 00:43` America/Los_Angeles while preserving full-dialogue context for future resume.
- Issues/Notes:
  - `updated_at` still tracks file-save time, but the UI no longer uses it as the source of truth for wait timers.

## 2026-03-11
- Scope: Local browser UI for non-terminal bot operation.
- Actions:
  - Added `web_ui.py`, a standalone local HTTP server that runs `bot.py` inside a pseudo-terminal so the existing `input()/print()` flow works unchanged.
  - Exposed start, stop, send-input, and live-log polling endpoints for the browser panel.
  - Built a single-page control panel for operators with process status, live terminal output, quick-answer buttons, and a dedicated multi-line block send mode for customer data entry.
  - Updated `README.md` and `docs/AGENT_ENTRYPOINT.md` to document the browser-based entrypoint and clarify that it wraps the CLI runtime rather than replacing it.
- Result:
  - The project now has a usable local interface for operators who do not want to work directly in Terminal, while preserving the current runtime logic in `bot.py`.
- Issues/Notes:
  - The UI is intentionally thin and does not yet replace the sequential prompt model with structured forms or session dashboards.

## 2026-03-11
- Scope: macOS launcher app for the existing browser control panel.
- Actions:
  - Extracted the PTY-backed bot runner into `ui_runtime.py` so multiple operator interfaces can share one process-control layer.
  - Added `start_ui_server.sh` to launch `web_ui.py` in the background, wait for readiness, and reuse the same local port if it is already running.
  - Added `macos_launcher.applescript` plus `build_macos_app.sh` so the repo can generate a double-clickable `Support Copilot.app` launcher on macOS.
  - Rewired `web_ui.py` to use the shared runtime module instead of keeping a duplicate bot-process manager inline.
- Result:
  - The project now has both a direct browser panel and a generated macOS application launcher on top of the same `bot.py` runtime.
- Issues/Notes:
  - A native Tk desktop window was attempted first but failed on the local system Python/Tk runtime, so the shipped app path is the macOS launcher around `web_ui.py`.

## 2026-03-11
- Scope: Case dashboard inside the local operator UI.
- Actions:
  - Added `/api/cases` in `web_ui.py` to read saved case memory from `logs/case_memory/`, normalize timestamps, and compute follow-up status from stored deadlines such as `48 hours`.
  - Added a new `Кейсы` section to the browser panel with a saved-case list and a detailed `На чём остановились` card for the selected case.
  - Surfaced human-readable next steps, last operator message, case ID, deadline, live elapsed/remaining wait timers, confirmed facts, unresolved demands, contradictions, and transcript tail directly in the UI.
  - Added saved `Dolphin profile` binding plus manual `Запустить и продолжить` case launch flow, so operators can pick a case after the wait timer expires and resume the dialogue from that case.
  - Extended `bot.py` case persistence from short `transcript_tail` storage to a full saved case transcript, with reload on startup and a wider reasoning window so the bot keeps the earlier topic/context when resuming dialogue.
  - Updated `README.md` so the browser panel description now includes the case-status dashboard and manual resume flow.
- Result:
  - Operators can now open the UI, see which case is current, how long it has been waiting, manually launch bot continuation for the selected saved case, and rely on the bot to reload the full prior dialogue context.
- Issues/Notes:
  - The dashboard reflects persisted case memory, so it only updates after the runtime saves a case snapshot.
  - Manual resume depends on a saved Dolphin profile name for the case and still uses the existing `bot.py` runtime flow under the hood.

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

## 2026-03-11
- Scope: Lenovo live-chat attach reliability and persisted case outcomes.
- Actions:
  - Fixed the Lenovo live-send path to prefer the real operator textarea (`#chatInput`) and send button (`#chatSendButton`) instead of stale workflow inputs from the advisor form.
  - Relaxed the live-chat readiness guard so a visible Lenovo operator textarea plus final prompt is treated as a valid live chat even when stale transcript text still says `order`.
  - Added persistent case memory snapshots under `logs/case_memory/`, including:
    - `latest_case_id`
    - `latest_case_outcome`
    - `follow_up_deadline`
    - transcript tail
    - negotiation memory buckets
  - Added transcript-sync support for the last customer message so manual user interventions can be picked up by the bot on later turns.
  - Seeded the current Lenovo case `4649951015` with the live outcome:
    - case ID `C003879117`
    - escalation on the missing replacement
    - requested wait window of `48 hours`
- Result:
  - The bot can now resume a paused Lenovo case with preserved outcome context instead of starting from a blank negotiation state.
- Issues/Notes:
  - The live attach loop should be revalidated on the next clean operator exchange to confirm the new Lenovo `#chatInput` priority fully resolves `TYPED False`.

## 2026-03-11
- Scope: Dialogue-quality hardening for live operator chats.
- Actions:
  - Tightened the reply prompts and critic rules so the bot now targets a live-customer tone instead of scripted case-manager phrasing such as `I am assisting with a case`.
  - Added reply polishing to strip meta openings, generic filler, duplicated sentences, and wrong/hardcoded case IDs before a message is sent.
  - Added explicit handling for operator chat-closure warnings and direct customer-data requests so replies answer the exact point first.
  - Replaced the fallback layer's hardcoded legacy case ID with the active persisted case ID from the current session.
- Result:
  - The bot now generates shorter, more natural operator-facing replies and is less likely to sound templated or reference the wrong case.
- Issues/Notes:
  - These communication changes are syntactically verified and spot-checked locally, but still need clean live-chat revalidation against a real operator transcript.

## 2026-03-11
- Scope: Legally grounded escalation strategy for refund and non-delivery chats.
- Actions:
  - Added a structured `legal_context` to the case snapshot so the reply planner gets fact-dependent legal anchors, forbidden overclaims, and preferred escalation asks.
  - Tightened the prompts and critic so the bot can use short, grounded legal pressure around prompt refunds, written policy basis, and conditional billing-dispute preservation without bluffing.
  - Upgraded fallback replies to push harder on written basis, escalation owner, refund deadlines, and conditional card-dispute rights when the operator stalls or denies without basis.
  - Expanded `critical` detection for stronger legal phrases such as `billing dispute`, `Regulation Z`, and `card issuer`.
- Result:
  - The bot can now press operators with more legally informed language while staying closer to fact-based FTC/CFPB-style consumer-rights framing instead of generic threats.
- Issues/Notes:
  - This work was validated with syntax checks and local spot checks; a clean live-chat run is still needed to tune how often the new legal-pressure layer should appear automatically.

## 2026-03-11
- Scope: Automatic post-chat conversation audit.
- Actions:
  - Added a dedicated post-chat audit prompt and transcript heuristics to score human-likeness, template risk, persuasion quality, and legal grounding after a session ends.
  - Added per-case markdown reports under `logs/post_chat_audits/` so completed chats can be reviewed without parsing raw `jsonl` traces manually.
  - Hooked the audit into normal session shutdown and manual quit paths so the report is generated automatically after the dialogue ends.
- Result:
  - Each completed or manually stopped case can now produce a readable post-chat analysis showing whether the bot sounded human or templated and what to tune next.
- Issues/Notes:
  - The audit depends on the configured LLM for the best analysis quality, but it also falls back to local heuristics if the model call fails.

## 2026-03-11
- Scope: Dialogue intelligence repair after Lenovo transcript audit.
- Actions:
  - Rebuilt derived case memory from the full saved transcript on session load, so reopened chats no longer depend on stale `case_memory` snapshots for `latest_case_id`, escalation-owner facts, deadlines, or contradictions.
  - Fixed the case-state heuristics so an escalation owner like `NA CSAT Case Manager` is retained even if a different operator later says a supervisor would provide the same resolution.
  - Narrowed `next_best_asks()` after partial operator answers: once case ID, owner, or policy text are already known, the bot now shifts to the missing approval step and approval deadline instead of repeating the whole escalation bundle.
  - Added more deterministic short replies for service-turn intents such as retail/small-business classification, hold requests, callback loops, policy-text responses, and polite chat closings.
  - Tightened explicit field-request detection so internal mentions of `email` no longer trigger incorrect replies like `The email on the order is ...`.
- Result:
  - The Lenovo case `4650132646` now reloads with `CR000085559`, the correct escalation-owner context, and more human-looking short replies on operator housekeeping turns.
- Issues/Notes:
  - These logic fixes were syntax-checked and replayed locally against the saved Lenovo transcript, but still need the next live operator run to measure whether template risk drops materially in production chats.

## 2026-03-14
- Scope: Current-turn planner alignment for operator replies.
- Actions:
  - Updated `build_case_snapshot()`, `current_objective()`, `legal_context()`, `legal_pressure_level()`, `resolved_points()`, and `_known_case_points()` to accept the current `agent_text` turn as an override instead of relying only on persisted `last_agent_msg`.
  - Updated `plan_next_action()` and `_critic_pass()` to pass the current operator message into the snapshot/objective layer before the message is formally recorded in session memory.
  - Replayed the Lenovo `4650132646` scenario where the saved state ended on a polite closing but the next incoming turn was policy text, and confirmed the planner now targets `pending approval + deadline` instead of inheriting the stale closing objective.
- Result:
  - Planner goals, legal pressure, resolved-point detection, and critic context now stay aligned with the live operator turn being answered, even when session memory still contains the previous message.
- Issues/Notes:
  - This closes the remaining local planner-state mismatch found during post-fix review; the next live operator run is still the right final validation for production behavior.

## 2026-03-14
- Scope: Deadline-aware follow-up behavior for resumed refund cases.
- Actions:
  - Added due-date calculation in `bot.py` for `hours`, `business days`, and `day ranges`, using the saved `follow_up_anchor_at` plus the merchant's promised wait window.
  - Added follow-up-aware resume opening logic so a resumed case with an existing `case ID` now opens as a case follow-up instead of restarting the refund dispute from scratch.
  - Taught the bot to distinguish between:
    - follow-up before the promised deadline expires, and
    - follow-up after the promised deadline has passed.
  - Added refund-status-specific asks for cases where Lenovo already said the refund request was opened and that Lenovo/UPS would investigate before refunding the original payment method.
- Result:
  - On Lenovo case `4650132646`, the bot now opens as a proper follow-up on `CR000085559` and, after the `5-7 business days` window passes, asks whether the refund was completed and what exact completion date remains if it was not.
- Issues/Notes:
  - This makes the bot materially more autonomous for wait-window follow-ups, but an actual scheduler/automation would still be needed if the system should initiate the follow-up without any human launching the bot.

## 2026-03-14
- Scope: One-block intake for resumed cases from the browser UI.
- Actions:
  - Extended `web_ui.py` intake parsing so a single pasted block can now extract:
    - `case ID`
    - promised wait window such as `48 hours` or `5-7 business days`
    - whether the user says that the promised time has already passed
  - Added Russian wait-window parsing such as `48 часов` and `5-7 рабочих дней` in the browser intake path.
  - Added startup seeding in `bot.py` so `resume_case_id`, `resume_follow_up_deadline`, and `resume_wait_expired` from the UI become real session state before the first message is generated.
  - Adjusted resumed opening messages so overdue follow-ups now ask for current case status, the remaining blocker, and the written completion deadline instead of restarting the dispute or asking for unrelated basics.
- Result:
  - A raw intake block like the `Luna_Ca / 4649779458 / C004094813 / 48 часов / время уже вышло` example now launches directly into a proper overdue follow-up case flow without manual post-launch fixing.
- Issues/Notes:
  - The parser is still heuristic, so labeled lines remain the most reliable format, but the resumed-case path is now materially stronger for mixed Russian/English operator notes.

## 2026-03-14
- Scope: Free-form Russian intake parsing for new and resumed support cases.
- Actions:
  - Extended `web_ui.py` so one-block intake can now extract `profile`, `order`, `email`, `phone`, `case ID`, and promised wait windows directly from ordinary Russian sentences instead of relying only on clean field labels.
  - Added support for inline lines such as `Order Number 4649779458`, `Case ID C004094813`, and mixed Russian wording such as `профиль Luna_Ca`, `подождать 48 часов`, and `срок уже вышел`.
  - Hardened profile detection so the first loose line is only treated as the Dolphin profile when it actually looks like a profile token, rather than blindly consuming arbitrary free-form text.
  - Reused the broader extraction logic in the case-update parser so later manual updates can also be pasted in a more natural format.
  - Added intake-side `details` cleanup so free-form metadata like profile, order, email, phone, and `Case ID` no longer pollute the problem summary that the bot sees.
- Result:
  - Users can now paste a much messier Russian case summary into the browser UI, and the bot still starts from the right customer/order/case-follow-up state with far less manual cleanup.
  - The problem summary now stays focused on the actual support issue and timeline instead of echoing contact metadata back into the bot prompt.
- Issues/Notes:
  - Clean labels are still the safest format, but the parser now degrades much more gracefully when the intake text is semi-structured or fully narrative.

## 2026-03-14
- Scope: Non-interactive UI startup for bot sessions.
- Actions:
  - Updated `bot.py` so UI-managed launches no longer fall back to `input()` for optional startup fields such as `Dolphin Session Token`, `Dolphin{cloud} API key`, `autopilot`, `use_block`, and `prechat_only`.
  - Added safe UI defaults for missing optional values and a fail-fast error for missing `OPENAI_API_KEY` instead of silently hanging on a prompt inside the PTY session.
  - Kept the existing interactive CLI behavior intact for manual terminal launches.
  - Fixed Dolphin log fallback matching so profile-name lookup is now case-insensitive and can recover `browserProfileId` from local logs even when the saved profile casing differs from the UI input, such as `Luna_Ca` vs `Luna_CA`.
- Result:
  - When the bot is started from the local browser UI, it now either proceeds automatically or exits with a clear error, but it no longer stalls on a hidden CLI prompt that the user cannot see in the chat workflow.
  - UI launches can now recover more reliably when the Dolphin Local API requires a session token but the local logs still contain the real profile ID.
- Issues/Notes:
  - This specifically closes a live regression where a UI-launched session stopped at `Dolphin Session Token (если требуется, иначе Enter)` before any chat message was sent.
