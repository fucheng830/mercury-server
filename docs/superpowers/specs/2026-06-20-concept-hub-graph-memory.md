# Concept-Hub Graph Memory — Design Spec

**Date:** 2026-06-20
**Goal:** Turn mercury's flat project-scoped memory store into an atomic-memory + concept-hub graph so that recalling one idea transitively triggers related memories across projects (knowledge compounding), without losing precise per-project injection.

## Background & Motivation

Current state: memories are atomic (extractor enforces one idea each) and project-scoped
(`recall_memories` returns a project's top-N). But there are **no links between memories** —
they are orphans in project buckets. "Recall A → trigger B/C/D" does not happen. The
`entities` / `relations` / `memory_entities` tables exist but are empty.

Cognitive-psych frame: memory = building retrieval cues, not storage. So associative recall
should be **pull-based** (triggered by the current problem's signals), not only the blind
top-N push done at SessionStart.

## Decisions (locked)

1. **Scope model — project-precise + optional cross-domain.** Nodes stay project-scoped
   (SessionStart recall unchanged). Cross-project association is opt-in via a new graph walk.
2. **Edge model — concept hubs (memory ↔ entity).** Each memory links to global concept
   nodes; memories sharing a concept are transitively linked through the hub. Reuses the
   existing `entities` / `memory_entities` tables. No memory↔memory pairwise comparison.
3. **Edge building — piggyback + periodic normalization.** The extraction LLM emits
   `concepts` per memory in the same pass (zero extra calls); `_write_extracted` upserts
   entities and links them. A monthly job normalizes/merges fragmenting entity names.

## Architecture

**Key insight:** the `entities` table has no `project_id` and `name` is globally unique —
concept hubs are **global** by construction, while `memories` remain project-scoped. The
global concept node is exactly the bridge that connects memories across projects. This is
why project precision and cross-discipline compounding do not conflict.

```
memories (project-scoped)        entities (global concept hubs)
   [mercury] BUGFIX pg %s  ───┐       (pgvector) ───┐
   [ragi]    ARCH DB creds ───┼──►  concept node ◄───┼──►  (shared hub links across projects)
   [hrms]    DISCOVERY img ───┘       (bge-m3)  ─────┘
                          memory_entities junction (already exists)
```

## Components

### 1. Build path — piggyback concept emission (`hermes/extractor.py`)
- `EXTRACT_SYSTEM_PROMPT`: each processed item now also emits `concepts: [...]` — 1-4 key
  nouns/topics this knowledge is *about*, normalized to canonical lowercase names (tool
  names, algorithms, techniques, domain terms) that could recur across other memories.
- `_write_extracted`: after `write_memory`, capture the returned `id`; for each concept call
  `upsert_entity(name, "concept", auto_embed=True)` then `link_memory_entity(id, name)`.
  Fall back to the item's `tags` if `concepts` is empty (forcing ≥1 link where possible);
  skip only if both empty.

### 2. Consumer path — associative recall (`hermes/memory_service.py`)
- `recall_memories(project)` and the SessionStart hook are **unchanged** (project top-N push).
- New `associate(query, hops=1, cross_project=True, limit=10, min_importance=1)`:
  embed query → top-k matching `entities` by vector similarity → `get_entity_memories` per
  entity (1-hop, optionally crossing projects) → optional 2-hop via entity co-occurrence.
  Returns deduplicated memories ranked by importance + hub match. This is the pull-based
  retrieval cue: triggered by the current problem, walks the graph.
- Exposed as MCP tool `associate_memory` and REST `POST /api/memory/associate`.

### 3. Quality — entity normalization (`hermes/iteration.py`)
- Extend `run_monthly_graph_maintenance`: after extracting entities, run an LLM clustering
  pass that groups near-duplicate entity names (`OAuth2` / `OAuth 2.0` / `PKCE-OAuth`),
  pick a canonical name, merge rows in `entities`, re-link `memory_entities`, and drop
  the superseded names. Prevents hub fragmentation (the failure mode that breaks compounding).

### 4. Backfill
- Run a one-shot concept extraction over the existing active memories (analogous to the
  embedding backfill), so the graph is populated immediately rather than waiting for new
  extraction.

## Non-Goals
- Do not change `recall_memories` semantics or the SessionStart hook.
- Do not abandon project partitioning.
- No frontend changes in this iteration.
- No memory↔memory direct edges (hub model only).

## Acceptance Criteria
1. A fresh extraction emits `concepts` and creates `entities` + `memory_entities` rows.
2. `associate("pgvector")` returns memories from multiple projects linked to that hub.
3. MCP `associate_memory` returns the same results as the REST endpoint.
4. Backfill links the pre-existing active memories to concept hubs.
5. The monthly normalization job merges a planted duplicate entity name.
