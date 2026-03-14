# Error Log

## Logging Rule
Document every meaningful runtime failure and mitigation.

## 2026-03-10 — Lenovo Mixed Widget State Caused Stale Transcript Loops
- Symptom:
  - The bot repeatedly treated hidden or expired Lenovo transcript state as the active workflow and looped on `Widget already open`, `Start a new chat`, or the wrong entry path instead of progressing.
- Cause:
  - Lenovo/Powerfront rendered multiple overlapping states at once:
    - hidden expired pane content,
    - visible top-level menu,
    - visible `Chat with an Agent` entry workflow,
    - and later active form fields.
  - The text reader and state classifier favored the longest transcript chunk instead of the visible actionable widget state.
- Mitigation:
  - Made Lenovo widget text extraction visibility-aware.
  - Added mixed-state routing for top-menu and `Chat with an Agent` entry screens.
  - Strengthened picklist clicking to target real `.picklistOption` elements.
  - Reworked form-step filling around the live-proven visible-input path and next-state verification.
- Status:
  - Improved and live-validated through greeting send and operator-transfer message on the `Katrin_NJ` run.

## 2026-03-06 — Wrong Target Field Input
- Symptom:
  - Bot typed into non-chat fields (example: country/region/search field).
- Cause:
  - Input matching was too permissive when chat widget state was ambiguous.
- Mitigation:
  - Added stricter chat-only input filtering and blocked known non-chat patterns.
- Status:
  - Mitigated, keep monitoring.

## 2026-03-06 — Chat Launcher Not Opening Reliably
- Symptom:
  - Chat launcher visible but widget did not open automatically.
- Cause:
  - Dynamic/late rendering + non-uniform clickable surface in Lenovo widget.
- Mitigation:
  - Added explicit Lenovo `Chat Now` flow, retries, and fallback click strategies.
- Status:
  - Improved but runtime-dependent; continue iterative hardening.

## 2026-03-07 — Floating Launcher Fallback Chose Wrong Element
- Symptom:
  - Blue Lenovo chat launcher was visible, but fallback clicking did not open the widget during a live run.
- Cause:
  - Fallback candidates were sorted to prefer the bottom-right launcher, but code selected the last element after sorting, effectively biasing toward the least relevant candidate.
- Mitigation:
  - Updated the fallback selection to click the first candidate after priority sorting.
- Status:
  - Fixed in code; pending live revalidation.

## 2026-03-06 — Unexpected Profile Restart During Testing
- Symptom:
  - Profile appeared to close/reopen during repeated bot restarts.
- Cause:
  - Test runs with restart-enabled flags in certain sessions.
- Mitigation:
  - Added stricter keep-profile behavior controls for default stable runs.
- Status:
  - Mitigated for standard startup mode.

## 2026-03-10 — Lenovo Advisor Form Step Not Filling On Full Runtime
- Symptom:
  - Full `python3 bot.py` runs reached Lenovo advisor form states like `(1 of 4) What's your name?` but stalled without entering data.
- Cause:
  - The active advisor inputs were rendered in the live chat frame with dynamic `aria-label` metadata, while the runtime still depended too much on brittle selectors and stale transcript text.
- Mitigation:
  - Switched advisor filling to direct control scanning across live frames using `aria-label`, `id`, and `type` matching.
  - Added immediate execution of visible Lenovo states when the widget is already open.
  - Hardened widget-open handling so empty shells re-trigger the Lenovo CTA instead of looping.
- Status:
  - Fixed and validated on the normal full runtime. Mixed stale transcript text near the final prompt should still be monitored as a secondary risk, but the original form-stall defect is closed.

## 2026-03-10 — Typing Indicator Misread As Operator Message
- Symptom:
  - Live dialogue loop treated `Advisor is typing` as a real operator reply and drafted an unnecessary answer.
- Cause:
  - Transcript reader filtered many Lenovo/Powerfront system fragments, but did not explicitly block typing-status text.
- Mitigation:
  - Added `Advisor is typing` / `agent is typing` / `is typing` filtering in the operator-message reader.
  - Added review-trace logging so future false positives can be audited from structured runtime data instead of only console output.
- Status:
  - Fixed in code; ready for live revalidation on the next clean operator exchange.

## 2026-03-10 — Third-Person Refund Replies And Auto-Mode Pause
- Symptom:
  - Live Lenovo replies sometimes spoke about the user as `the customer`, and the runtime could stall mid-chat on the manual prompt `Продолжить диалог? [y/N]:`.
- Cause:
  - Prompting and fallback logic still allowed third-person case phrasing in some negotiation branches, and the final-stage continuation guard still defaulted to interactive confirmation.
- Mitigation:
  - Hardened prompts, critic checks, and reply sanitization to enforce first-person singular wording.
  - Added explicit handling for `return required before refund`, `retrieve and return again`, and soft-stall rhetoric so replies target the operator's actual claim.
  - Disabled the manual continuation stop when full auto mode is active.
- Status:
  - Fixed in code; pending clean live-chat revalidation.

## 2026-03-11 — Lenovo Live Chat Readable But Not Sendable (`TYPED False`)
- Symptom:
  - In attach/live mode the bot could read and reason about new operator replies, but `type_message()` returned `False` and no reply was inserted into the already-open Lenovo chat.
- Cause:
  - Lenovo kept stale advisor-form state in the transcript, so the runtime still prioritized workflow inputs and a too-strict chat-open guard instead of the real live operator textarea `#chatInput`.
- Mitigation:
  - Updated Lenovo live-send logic to prioritize `#chatInput` and `#chatSendButton`.
  - Relaxed live-chat readiness checks so visible `#chatInput` plus the final prompt are accepted as a live operator chat even if stale transcript text still says `order`.
  - Added persistent case memory and customer transcript sync so attach-mode can resume with the latest known case state and manual user messages.
- Status:
  - Fixed in code; pending clean live revalidation on the next operator exchange.

## 2026-03-11 — Scripted Tone And Wrong Case IDs In Operator Replies
- Symptom:
  - Some replies still sounded like a scripted case manager instead of a live customer, using phrases such as `I am assisting with a case`, generic filler openings, or overly broad case restatements.
  - Fallback replies could also reuse a hardcoded legacy case ID in unrelated chats.
- Cause:
  - Prompting and sanitization enforced first-person wording, but did not yet strip meta phrasing, filler openers, duplicated sentences, or wrong case-ID reuse.
  - Intent handling also treated some chat-closure warnings and data-request turns too generically.
- Mitigation:
  - Tightened the generation and critic prompts around direct live-chat tone.
  - Added reply polishing to normalize case IDs, remove scripted/meta phrasing, and trim filler before sending.
  - Added explicit intent handling for chat-closure warnings and direct field requests so replies answer the latest operator point first.
- Status:
  - Fixed in code; locally spot-checked and pending clean live-chat revalidation.

## 2026-03-11 — Stale Case Memory And Over-Broad Field Detection Caused Templated Repeats
- Symptom:
  - Reopened Lenovo chats could lose the active `CR...` case ID, escalation owner, and other already-resolved facts even though they were present in the saved transcript.
  - The bot then kept re-asking for case ID, owner, or policy text and looked more like a template than a human.
  - Operator messages that merely mentioned internal email workflows could incorrectly trigger replies like `The email on the order is ...`.
  - Housekeeping turns such as hold requests or polite closings could still fall back to a generic DOA/RNR pressure bundle instead of a short human reply.
- Cause:
  - Session startup trusted stale `case_memory` snapshots more than the full transcript, and the escalation-owner heuristic was accidentally neutralized by unrelated `same resolution` wording later in the chat.
  - Field-request detection treated some generic `email` mentions too broadly.
  - A few narrow operator intents were still routed through generic fallback logic.
- Mitigation:
  - Rebuilt derived case state from the full saved transcript on load and persisted the repaired memory back to disk.
  - Fixed escalation-owner detection and shifted `next_best_asks()` toward the missing approval step and approval deadline once case ID/owner/policy were already known.
  - Added deterministic short replies for service-turn intents and moved polite-closing handling ahead of generic case fallbacks.
  - Tightened explicit field-request matching so only real customer-data requests trigger account-detail replies.
- Status:
  - Fixed in code; syntax-checked and replayed locally against Lenovo case `4650132646`.

## 2026-03-14 — Planner Snapshot Could Lag One Operator Turn Behind
- Symptom:
  - During reply planning, the outgoing message could be generated from the current operator text, but the embedded case snapshot and `goal` field could still reflect the previous operator turn.
  - In practice this showed up on Lenovo case `4650132646`: after a saved polite-closing turn, a new policy-text message still produced a stale closing-oriented planner goal.
- Cause:
  - `plan_next_action()` and `_critic_pass()` built their case snapshot from persisted `last_agent_msg` before the current `agent_text` had been fully recorded in session memory.
- Mitigation:
  - Added current-turn overrides to the snapshot/objective layer so `build_case_snapshot()`, `current_objective()`, `legal_context()`, `legal_pressure_level()`, `resolved_points()`, and `_known_case_points()` can reason from the active `agent_text` immediately.
  - Updated planner and critic calls to pass the current operator message through that override path.
- Status:
  - Fixed in code; syntax-checked and replayed locally on the Lenovo transcript regression case.

## 2026-03-14 — Resumed Cases Restarted The Dispute Instead Of Following Up By Deadline
- Symptom:
  - When reopening a case that already had a `case ID` and a merchant-provided wait window like `24-48 business hours` or `5-7 business days`, the bot could still open the next chat as if it were a brand-new refund dispute.
  - Even after Lenovo said a refund request was opened and a `5-7 business days` refund window applied, the bot did not know by itself how to switch to a stronger overdue follow-up once that window passed.
- Cause:
  - Resume detection knew that a prior case existed, but the first-turn message did not use due-date logic and did not distinguish `still waiting within the window` from `the promised window has passed`.
  - Older `approval pending` asks could also outrank newer `refund requested / UPS investigation` facts from the latest merchant update.
- Mitigation:
  - Added due-date calculation for `hours`, `business days`, and day ranges using `follow_up_anchor_at`.
  - Switched resumed first-turn behavior to a dedicated case follow-up opening keyed off the saved `case ID`, wait window, and transcript context.
  - Added refund-status-specific follow-up asks so, after a promised refund window passes, the bot now asks whether the refund was completed and what exact completion date remains if it was not.
- Status:
  - Fixed in code; locally replayed on Lenovo case `4650132646` for both in-window and overdue follow-up scenarios.

## 2026-03-14 — One-Block Intake Did Not Promote Resume Metadata Into Real Bot State
- Symptom:
  - A new case pasted into the browser UI could include a prior `case ID`, a promised wait window, and even a note that the promised time had already passed, but the launched bot still treated it too much like a fresh case.
  - Russian phrases such as `48 часов` were not reliably recognized as a real follow-up deadline.
- Cause:
  - The UI intake parser mainly extracted customer/order fields and left resume metadata inside free-form `details`.
  - Startup config did not seed those resume fields into `CopilotSession` before the first outbound message.
- Mitigation:
  - Extended the browser intake parser to extract `resume_case_id`, `resume_follow_up_deadline`, and `resume_wait_expired`.
  - Added mixed Russian/English wait-window parsing for hours, days, and business days.
  - Added session seeding so the bot receives that metadata as real state before generating the first message.
- Status:
  - Fixed in code; verified locally on the `Luna_Ca / 4649779458 / C004094813 / 48 часов / время уже вышло` intake example.

## 2026-03-14 — Free-Form Russian Intake Still Needed Too Many Explicit Labels
- Symptom:
  - The browser intake worked well for neatly labeled blocks, but much messier Russian text could still lose important fields when the user pasted them inline inside normal sentences.
  - Lines such as `Order Number 4649779458` or `Case ID C004094813` were reliable, but more narrative phrasing like `профиль Luna_Ca ... подождать 48 часов ... срок уже вышел` still depended too much on lucky line boundaries.
  - Even when extraction succeeded, the raw `details` field could still contain duplicated profile/contact metadata, which weakened the problem summary passed into the bot.
- Cause:
  - Intake parsing mainly relied on exact label matches and line-by-line heuristics instead of scanning the whole pasted block for structured tokens embedded in ordinary prose.
  - The first loose line was also treated as the Dolphin profile too aggressively, which could misclassify arbitrary narrative text as a profile name.
  - After extraction, the intake flow did not scrub already-promoted metadata back out of the final `details` string.
- Mitigation:
  - Added whole-text extraction for `profile`, `email`, `phone`, `order number`, `case ID`, wait windows, and expired-wait markers.
  - Added inline label parsing for same-line values such as `Order Number 4649779458` and `Case ID C004094813`.
  - Tightened profile detection so only token-like profile strings are promoted into `profile_name`.
  - Reused the same broader extraction helpers in the case-update parser.
  - Added `details` cleanup so extracted profile/order/contact/`Case ID` metadata is removed from the support-issue summary while preserving the actual problem narrative and wait-window context.
- Status:
  - Fixed in code; syntax-checked and locally replayed against semi-structured and free-form Russian intake examples.

## 2026-03-14 — UI-Launched Bot Session Could Stall On Hidden CLI Prompts
- Symptom:
  - A bot session started from the local browser UI could appear to be running, but no messages were sent and no live transcript was created.
  - In the runtime PTY buffer, the process was blocked on `Dolphin Session Token (если требуется, иначе Enter):`, which the user could not answer from the normal UI flow.
- Cause:
  - `bot.py` still used CLI `input()` fallbacks for several optional startup fields whenever they were absent from `run_config`.
  - UI-launched sessions are non-interactive from the user's point of view, so those prompts effectively deadlocked the session before the first message.
- Mitigation:
  - Added explicit UI-managed startup behavior that disables interactive fallback prompts for optional fields and uses safe defaults instead.
  - Added a fail-fast error for missing `OPENAI_API_KEY` in UI mode, so configuration issues surface clearly instead of hanging.
  - Fixed the local Dolphin log fallback to match profile names case-insensitively, so profiles like `Luna_Ca` can still recover `browserProfileId` from log entries stored as `Luna_CA`.
- Status:
  - Fixed in code; detected from a live stalled UI session and verified locally after the patch.

## 2026-03-14 — Bot Could Send A Reply Planned Against Stale Operator Context
- Symptom:
  - During a live chat, the bot could read one operator message, generate a reply, wait a human-like delay, and still send that reply even if the operator posted a newer message during the delay.
  - This made the bot look like it was not reading the chat carefully and increased template-like behavior in fast operator exchanges.
- Cause:
  - The send path in `run_session()` planned only once per detected operator message and did not re-read the live operator context immediately before `send_message()`.
  - The pending reply had already been written into transcript/history state before the send was confirmed.
- Mitigation:
  - Added a pre-send live-context recheck, chat-input clearing, and rollback of the pending unsent customer message when the operator context changes before send.
  - Added a resume handoff so the fresh operator message is immediately fed back into the main loop for replanning.
- Status:
  - Fixed in code and syntax-checked locally.

## 2026-03-14 — Drop-Off Discrepancy Claims Were Treated As Generic Stalls
- Symptom:
  - When Lenovo replied with a concrete claim like `the package was dropped off 1,405 miles away from the intended shipping address`, the bot often answered with the old generic escalation bundle instead of addressing that specific claim.
  - This made the bot feel templated and under-reactive to new operator facts.
- Cause:
  - There was no dedicated intent for carrier/drop-off discrepancy language, so the planner and fallback logic collapsed these messages into generic pressure modes.
- Mitigation:
  - Added a dedicated `dropoff_location_claim` intent and updated objective selection, next asks, deterministic reply generation, and intent-coverage checks around it.
  - The new reply path now demands issue classification, keeps the UPS review internal to Lenovo, and asks for the responsible team plus refund-decision deadline.
- Status:
  - Fixed in code; verified locally against the Lenovo `1,405 miles away` message pattern.
