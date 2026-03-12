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
