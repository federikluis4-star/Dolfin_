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
