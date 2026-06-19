# Agent-First Project Memory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild memory capture (durable-knowledge extractor with reconciliation) and recall (SessionStart auto-injection) so Claude Code gets a project's relevant memories injected on session start.

**Architecture:** New `hermes/extractor.py` extracts 5 categories of durable knowledge from raw sessions (via existing `aggregate_daily`) + reconciles against the project's existing active memories (dedup/supersede/new). New `/api/memory/recall` endpoint + a Claude Code SessionStart hook injects a project's Top-N active memories into the agent context.

**Tech Stack:** Python/FastAPI (mercury-server), pgvector/PostgreSQL, LLM via LLMService (gpt-5.5), Claude Code SessionStart hook (shell + settings.json).

**Spec:** `docs/superpowers/specs/2026-06-20-memory-system-agent-first-design.md`

---

## File Structure

- **Create** `hermes/extractor.py` — durable-knowledge extractor + reconciler (extract_session_set → reconcile → write memories). Single responsibility: turn sessions into self-consistent project memories.
- **Create** `scripts/recall_hook.sh` (deployed alongside) — Claude Code SessionStart hook: curl `/api/memory/recall` for cwd, print context block.
- **Modify** `hermes/memory_service.py` — add `recall_memories(project_path, namespace, limit)` + `supersede_memory(id)` + `merge_into(existing_id, ...)` helpers.
- **Modify** `server.py` — add `POST /api/memory/extract` (trigger extractor for a date/project) + `GET /api/memory/recall`.
- **Modify** `recap/scheduler.py` — daily job calls extractor (replaces `run_server_recap` observation-writing for new memories).
- **Modify** `hermes/server_recap.py` — stop writing observations from recap (keep recap as UI artifact). Add a flag/guard.
- **Modify** `mercury-server` deploy: add the SessionStart hook to Claude Code `settings.json`.

---

## Phase 1 — Capture

### Task 1: `recall_memories` + `supersede_memory` helpers in memory_service

**Files:** Modify `hermes/memory_service.py`

- [ ] Add `recall_memories(project_path, namespace="claude", limit=30, min_importance=1)`:
  - Resolve project_id by path OR name (`SELECT id FROM projects WHERE path=%s OR name=%s LIMIT 1`); return `[]` if none.
  - `SELECT {_MEM_COLUMNS} FROM memories WHERE project_id=%s AND stage='memory' AND status='active' AND namespace=%s AND importance>=%s ORDER BY importance DESC, updated_at DESC LIMIT %s`.
- [ ] Add `supersede_memory(memory_id, namespace="claude")`: `UPDATE memories SET status='superseded', updated_at=now WHERE id=%s AND namespace=%s`.
- [ ] Add `bump_memory(memory_id, content=None, importance=None)`: increment recall_count, update updated_at, optionally overwrite content/importance (for merge). Returns updated row.
- [ ] py_compile + a quick inline smoke test against the local pgvector container (recall on a project with the 8 backfilled... note: existing data has project_id=NULL, so recall returns [] until extractor populates project_id — expected).

### Task 2: `hermes/extractor.py` — EXTRACT + RECONCILE prompts + extract_for_date

**Files:** Create `hermes/extractor.py`

- [ ] Define `EXTRACT_SYSTEM_PROMPT` (from spec §3) — extract 5 durable categories + confidence, reject noise, JSON output `{"processed":[{content,type,importance,confidence,tags}]}`.
- [ ] Define `RECONCILE_SYSTEM_PROMPT` — given new items + existing project memories (id+type+content), classify each new item as `new` / `duplicate_of:<id>` / `supersedes:<id>`. JSON `{"actions":[{new_item_index, action, target_id?}]}`.
- [ ] `def _sessions_text_for_project(sessions)`: build a readable transcript from a list of SessionSummary (project, user_prompts, assistant_summaries, tools_used) — one block per session.
- [ ] `def extract_for_project(sessions, project_name, project_path, llm, namespace="claude")`:
  1. text = `_sessions_text_for_project(sessions)`; if too short → return 0.
  2. `items = llm.generate_json(EXTRACT_SYSTEM_PROMPT, text).get("processed", [])`.
  3. Resolve/create project: `project_id = get_or_create_project(project_name, project_path)["id"]`.
  4. Reconcile: existing = `list_memories(stage="memory", project_id, statuses=["active"], size=50)`; if existing → `actions = llm.generate_json(RECONCILE_SYSTEM_PROMPT, new_items+existing)`.
  5. Apply actions: new(high conf)→write_memory(stage=memory,project_id,type,importance); new(low)→candidate; duplicate_of→bump_memory(target); supersedes→supersede_memory(target)+write new.
  6. Return counts.
- [ ] `def extract_for_date(date, llm, provider)`: `daily = aggregate_daily(provider, date)`; group `daily.sessions` by project; for each project call `extract_for_project`. Return summary.
- [ ] py_compile.

### Task 3: Wire extractor into server + scheduler; deprecate recap→observation

**Files:** Modify `server.py`, `recap/scheduler.py`, `hermes/server_recap.py`

- [ ] `server.py`: add `POST /api/memory/extract` — body `{date}` or `{project}`; calls `extract_for_date(date, _llm_service, _get_claude_provider())`; returns counts. (Manual trigger / test.)
- [ ] `recap/scheduler.py` daily job: after `generate_recap`, call `extract_for_date(today, llm, provider)` (new memories). Keep recap generation (UI).
- [ ] `hermes/server_recap.py`: guard the `write_memory(stage="observation")` calls behind an env/flag `MERCURY_WRITE_OBSERVATIONS` defaulting to false (observations no longer produced from recaps). Set nothing in compose → defaults off.
- [ ] py_compile + restart container; trigger `/api/memory/extract` for a recent date; verify memories written with project_id + types + reconciliation (run twice → second run should dedup/supersede, not duplicate).

## Phase 2 — Recall

### Task 4: `/api/memory/recall` endpoint

**Files:** Modify `server.py`

- [ ] `GET /api/memory/recall?project=<path>&namespace=claude&limit=30` → `recall_memories(...)`; return `{"project":..., "count":N, "items":[...]}`.
- [ ] py_compile; curl test (returns [] until extractor populates, then real items).

### Task 5: SessionStart hook script + Claude Code config

**Files:** Create `scripts/recall_hook.sh`; document settings.json edit

- [ ] `scripts/recall_hook.sh`: read cwd; `curl -s "http://127.0.0.1:8788/api/memory/recall?project=$(urlencode "$PWD")&limit=30"`; if items, print a formatted context block (`## 项目记忆…` with `[TYPE★imp] content` lines); else print nothing.
- [ ] Provide the `settings.json` SessionStart hook entry (the user adds it; or I add to their `~/.claude/settings.json` with confirmation). Hook command: `bash /path/to/recall_hook.sh`.
- [ ] Verify: in a project that has memories, the hook outputs a context block; `claude` session start shows it.

## Phase 3 — Historical re-extraction (lighter)

### Task 6: Batch re-extract + archive old

**Files:** Create `scripts/reextract_history.py` (one-off, like backfill)

- [ ] Iterate dates with sessions (or per-project), call `extract_for_date` for each historical date (batched, gpt-5.5).
- [ ] After re-extract: archive old noisy data — `UPDATE memories SET status='archived' WHERE source IN ('recap','server_recap','client_push','backfill_distill','seed') AND stage IN ('observation','candidate')` (keep new extractor-produced active memories).
- [ ] Verify counts: active memories per project; observations archived.

---

## Verification (验收)

- **Capture**: `/api/memory/extract` on a real recent date → memories created per project, typed (5 cats), project_id set. Run twice → reconciliation (no dups; supersede works on a synthetic conflict).
- **Recall**: `/api/memory/recall?project=<real project path>` → returns that project's active memories.
- **End-to-end**: SessionStart hook in a project with memories → agent session starts with the memory block injected.
- **Self-consistency**: no duplicate/contradictory active memories per project after multiple extracts.

## Self-Review (done)
- Spec coverage: capture(§3/3.1)=Tasks1-3; recall(§4)=Tasks4-5; historical(§6)=Task6; model(§5)=helpers in Task1; stage policy=Task2 apply-actions. ✓
- No placeholders: prompts + function bodies specified. ✓
- Type consistency: `recall_memories`, `supersede_memory`, `bump_memory`, `extract_for_project`, `extract_for_date` names stable across tasks. ✓
