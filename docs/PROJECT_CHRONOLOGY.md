# Project Chronology

## Format
Use one entry per significant work block.

- Date (YYYY-MM-DD)
- Scope
- Actions
- Result
- Issues/Notes

---

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

## 2026-03-05
- Scope: Environment/config and project baseline docs.
- Actions:
  - Added `.env` support and `.gitignore` protection for secrets.
  - Added `.env.example` and updated README for environment-based startup.
- Result:
  - Safer local configuration workflow established.
- Issues/Notes:
  - Remote `origin` may be missing in local repository setup.
