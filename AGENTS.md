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

## Expected workflow
1. Implement change.
2. Update docs.
3. Commit.
4. Push.

## Enforcement
Git hooks in `.githooks/` enforce these rules.
