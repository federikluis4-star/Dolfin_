# AGENTS Policy (Repository Guardrails)

This repository enforces mandatory documentation discipline for any coding agent/human contributor.

## Mandatory First Read
Before making changes, read:
1. `docs/AGENT_ENTRYPOINT.md`
2. `docs/PROJECT_CHRONOLOGY.md`
3. `docs/ERROR_LOG.md` if working on a fix, regression, or runtime incident

## Non-negotiable rules
1. Before every commit, update:
   - `docs/PROJECT_CHRONOLOGY.md` (required if code changed)
   - `docs/ERROR_LOG.md` (required if commit is a fix/bug/error/incident)
2. Before every push:
   - Ensure commit/push policy is respected.
   - Do not push secrets (`.env`, credentials, tokens).
3. Do not bypass hooks unless explicitly approved by repository owner.
4. For live browser work, prefer normal runtime execution over ad-hoc escalated probes:
   - first choice: `python3 bot.py` with stronger runtime logs;
   - second choice: code-level instrumentation inside `bot.py`;
   - last resort only: one-off CDP/Playwright live probes against the running browser.
5. When working on Lenovo or other fragile chat widgets:
   - minimize actions that require extra system approvals;
   - avoid repeated diagnostic commands against the live browser when the same information can be surfaced through bot logs;
   - fix the runtime path so future runs need fewer external probes.
6. Assume the repository owner wants autonomous execution:
   - do not stop for ordinary run/edit/test confirmations;
   - only rely on approval prompts when the sandbox itself requires them.

## Expected workflow
1. Implement change.
2. Update docs.
3. Commit.
4. Push.

## Enforcement
Git hooks in `.githooks/` enforce these rules.
