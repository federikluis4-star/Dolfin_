# Agent Entrypoint

## Purpose
This document is the mandatory first read for any new coding agent or contributor in this repository.

Goal:
- explain what the project is;
- explain what is already implemented;
- explain what is currently in flight;
- explain how to work without breaking chronology, docs, or repository discipline.

Read this before editing code, running flows, or preparing commits.

## Project Mission
`dolphin-bot` is a browser-support copilot for live customer-service chats.

Current intended flow:
1. Connect to a Dolphin Anty browser profile through Local API.
2. Attach to the opened browser with Playwright over CDP.
3. Open or recover the relevant support chat page/widget.
4. Read support-agent messages from the DOM.
5. Generate the next reply with the OpenAI Chat Completions API.
6. Insert the generated reply into the chat input.
7. Keep a human approval step before actual send, unless automation rules are deliberately expanded later.

## Current Repository Shape
Main files:
- `bot.py`: the entire runtime application.
- `README.md`: operator-facing setup and run instructions.
- `AGENTS.md`: repository guardrails and mandatory documentation rules.
- `docs/PROJECT_CHRONOLOGY.md`: chronological project log.
- `docs/ERROR_LOG.md`: runtime failures and mitigations.
- `docs/COMMIT_PUSH_POLICY.md`: commit/push discipline.
- `.githooks/`: local enforcement for docs-before-commit and docs-before-push.

There is no package layout yet, no test suite yet, and no formal module split yet.

## Tech Stack
- Python
- Playwright
- Requests
- Dolphin Anty Local API
- Chrome DevTools Protocol (via Playwright `connect_over_cdp`)
- OpenAI Chat Completions API

Dependency state from repository files:
- `requirements.txt` currently lists `playwright`, `anthropic`, and `requests`.
- Runtime code in `bot.py` is already migrated from OpenRouter to OpenAI.
- `README.md` and `.env.example` also contain local uncommitted migration changes toward OpenAI.

This means documentation and runtime direction are aligned toward OpenAI, but dependency cleanup is not fully complete yet.

## Architecture Snapshot
Logical layers inside `bot.py`:
- Environment loading and runtime flags.
- Dolphin profile discovery/start/stop helpers.
- LLM session state (`CopilotSession`).
- DOM reading and writing helpers for chat widgets.
- Lenovo-specific chat-opening hardening.
- Interactive CLI flow for session startup and operator review.

Important current implementation traits:
- The codebase is highly stateful and procedural.
- Store-specific handling is mostly selector-driven.
- Lenovo flow is the most developed and most brittle integration.
- The product currently depends on runtime heuristics rather than deterministic page contracts.

## What Is Already Implemented
From committed history and current working tree:
- `.env`-based configuration exists.
- Secrets are protected by `.gitignore` and git hooks.
- Repository guardrails already enforce chronology/error-log discipline.
- OpenAI API usage is present in the working tree.
- The bot can parse profile names for store/case hints.
- The bot attempts to open chat widgets, detect operator messages, type replies, and send them.
- Lenovo chat opening has dedicated handling, retries, and stricter widget validation.
- The bot can parse pasted customer blocks with name/order/email/phone fields.
- There is a `prechat_only` mode in the working tree for advancing only to the pre-chat stage.

## Current Working Tree Status
Uncommitted changes already exist and must be preserved:
- `bot.py` is modified.
- `README.md` is modified.
- `.env.example` is modified.
- `install.sh` is untracked.
- `requirements.txt` is untracked.

Do not assume `HEAD` reflects the real current project state. Always inspect `git status` and `git diff` first.

## Operational Constraints
- The repository root for git work is `/Users/lev/Downloads/support-agent/dolphin-bot`.
- The parent folder is not the git root.
- Git hooks are part of the required workflow and should not be bypassed.
- `.env` must never be committed.
- Browser/chat automation is inherently runtime-sensitive; timing and page state matter.
- Sandbox verification may block standard Python bytecode compilation because `.pyc` writes target a system cache outside the writable area.

## Mandatory Start Routine For Any New Agent
1. Open `AGENTS.md`.
2. Open this file.
3. Run `git status --short`.
4. Read `docs/PROJECT_CHRONOLOGY.md`.
5. Read `docs/ERROR_LOG.md` if working on a bug, regression, or automation issue.
6. Inspect current diffs before editing files that are already dirty.
7. Only then edit code or docs.

## Mandatory End Routine For Any Agent Making Changes
1. Update `docs/PROJECT_CHRONOLOGY.md` if code or significant project docs changed.
2. Update `docs/ERROR_LOG.md` if the work fixes or documents a failure/regression/incident.
3. Re-check `git status --short`.
4. Verify that no secrets are staged.
5. Commit with a scope that matches reality.

## Working Agreements For Future Threads
When continuing project work, each new thread should establish:
- current branch;
- current dirty files;
- target support flow/store being worked on;
- whether work is product code, runtime investigation, or documentation/process;
- what changed since the last chronology entry.

If the thread produces meaningful new knowledge, record it in docs before ending work.

## Recommended Near-Term Refactor Plan
The project is usable as a prototype, but the next durable steps are:
1. Split `bot.py` into modules:
   - Dolphin API client
   - Chat/store adapters
   - LLM client
   - CLI/session orchestration
2. Replace ad-hoc selector logic with store-specific adapters.
3. Add a reproducible test layer for parsers and non-browser logic.
4. Add a formal runbook for authentication/login handling.
5. Add a session handoff template for live support operations.

## Gaps Still Open
- No automated test suite.
- No dedicated authentication/login runbook.
- No persisted conversation/session memory outside the running process.
- No stable abstraction for multi-store support.
- No explicit policy document yet for what the bot may send autonomously versus what must stay human-approved.
- `install.sh` still reflects Anthropic-era setup and needs reconciliation with the current OpenAI direction.
- `requirements.txt` and runtime code are not yet fully cleaned up to one provider story.

## Commit Hygiene Notes
This repository already expects every meaningful work block to be reflected in chronology.

Good commit shape:
- one coherent implementation step;
- matching chronology entry;
- matching error-log entry if the commit addresses a failure.

Bad commit shape:
- code-only commit with no chronology update;
- “fix” commit without updating `docs/ERROR_LOG.md`;
- mixing unrelated runtime experiments and documentation changes without a clear scope.

## Decision Rule
If there is any conflict between:
- current working tree state,
- old README wording,
- and old committed chronology,

trust the inspected current code plus current git diff first, then update docs so the repo becomes self-consistent again.
