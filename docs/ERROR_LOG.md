# Error Log

## Logging Rule
Document every meaningful runtime failure and mitigation.

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

## 2026-03-06 — Unexpected Profile Restart During Testing
- Symptom:
  - Profile appeared to close/reopen during repeated bot restarts.
- Cause:
  - Test runs with restart-enabled flags in certain sessions.
- Mitigation:
  - Added stricter keep-profile behavior controls for default stable runs.
- Status:
  - Mitigated for standard startup mode.
