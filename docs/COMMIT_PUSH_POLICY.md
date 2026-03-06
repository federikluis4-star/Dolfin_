# Commit/Push Policy

## Purpose
This policy is mandatory for every commit/push in this project.

## Mandatory Rule Before Commit
Before each commit, update documentation with:
- What was implemented (features/fixes).
- What failed or behaved unexpectedly.
- What was changed to resolve issues.
- Remaining risks / known limitations.

## Mandatory Rule Before Push
Before each push:
1. Ensure docs are updated.
2. Ensure chronology file has a new entry for current work.
3. Ensure error log is updated (if there were incidents/regressions).
4. Ensure sensitive data is not staged (`.env`, secrets, credentials).

## Commit Checklist
- Code changes are intentional and tested at least minimally.
- Docs updated:
  - `docs/PROJECT_CHRONOLOGY.md`
  - `docs/ERROR_LOG.md` (if applicable)
- Commit message describes actual scope.

## Push Checklist
- Correct branch selected.
- No secrets in staged diff.
- Chronology + errors documentation included in commit.
- Push executed only after checklists pass.
