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
  - Mitigated. Full runtime now reaches chat-ready and sends the first message, but mixed stale transcript text near the final prompt should still be monitored.
