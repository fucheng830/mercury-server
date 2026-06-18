CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    layer       VARCHAR(10) NOT NULL CHECK (layer IN ('episodic', 'semantic', 'core')),
    content     TEXT NOT NULL,
    summary     TEXT,
    source      VARCHAR(50) DEFAULT 'recap',
    importance  SMALLINT DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    tags        TEXT[] DEFAULT '{}',
    embedding   vector(1024),
    fts         tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    recall_count INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now(),
    expires_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);
CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memories_fts ON memories USING gin(fts);
CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS entities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(200) NOT NULL UNIQUE,
    entity_type VARCHAR(50) NOT NULL,
    description TEXT,
    embedding   vector(1024),
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_embedding ON entities USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS relations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation    VARCHAR(100) NOT NULL,
    strength    REAL DEFAULT 1.0 CHECK (strength BETWEEN 0 AND 1),
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_unique ON relations(source_id, target_id, relation);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (memory_id, entity_id)
);

-- hybrid_search v2 (multi-filter + pagination) is defined in the
-- Memory Model v2 migration section at the end of this file,
-- after the stage/type/scope/status/project_id columns exist.

-- ── Client-Server Tables ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS clients (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL UNIQUE,
    hostname    VARCHAR(100),
    os_info     VARCHAR(50),
    created_at  TIMESTAMPTZ DEFAULT now(),
    last_sync   TIMESTAMPTZ,
    last_heartbeat TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sync_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    file_path   VARCHAR(500) NOT NULL,
    file_size   BIGINT,
    file_mtime  TIMESTAMPTZ,
    session_count INTEGER DEFAULT 0,
    synced_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE(client_id, file_path)
);

CREATE TABLE IF NOT EXISTS sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project_id  VARCHAR(200) NOT NULL,
    session_id  VARCHAR(100) NOT NULL,
    project_path TEXT,
    preview     TEXT,
    message_count INTEGER DEFAULT 0,
    token_input   BIGINT DEFAULT 0,
    token_output  BIGINT DEFAULT 0,
    embedding   vector(1024),
    first_ts    TIMESTAMPTZ,
    last_ts     TIMESTAMPTZ,
    file_mtime  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(client_id, project_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_client ON sessions(client_id);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_last_ts ON sessions(last_ts DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_embedding ON sessions USING hnsw (embedding vector_cosine_ops);

-- ── Namespace (Multi-Agent Isolation) ──────────────────────────

ALTER TABLE memories ADD COLUMN IF NOT EXISTS namespace VARCHAR(50) DEFAULT 'claude';
CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace);
CREATE INDEX IF NOT EXISTS idx_memories_ns_layer ON memories(namespace, layer);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS namespace VARCHAR(50) DEFAULT 'claude';

-- ── A2A Agents ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS a2a_agents (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id         VARCHAR(100) NOT NULL UNIQUE,
    name             VARCHAR(200) NOT NULL,
    description      TEXT,
    namespace        VARCHAR(50) NOT NULL,
    agent_url        TEXT,
    auth_scheme      VARCHAR(20) DEFAULT 'bearer',
    auth_credentials TEXT,
    capabilities     JSONB DEFAULT '{}',
    permissions      JSONB DEFAULT '{
        "read_own": true,
        "write_own": true,
        "read_shared": true,
        "write_shared": true
    }',
    rate_limit       INTEGER DEFAULT 60,
    created_at       TIMESTAMPTZ DEFAULT now(),
    last_active      TIMESTAMPTZ,
    enabled          BOOLEAN DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_a2a_agents_namespace ON a2a_agents(namespace);

-- ════════════════════════════════════════════════════════════════════════
-- Memory Model v2 Migration (idempotent — runs every startup via init_db)
-- observation → candidate → memory workflow + type/scope/status/project
-- See docs/superpowers/specs/2026-06-18-memory-model-v2-design.md
-- ════════════════════════════════════════════════════════════════════════

-- ── projects ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       VARCHAR(200) NOT NULL,
    path       TEXT,
    namespace  VARCHAR(50) NOT NULL DEFAULT 'claude',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(namespace, name)
);

-- ── type_registry (extensible memory-type enum) ───────────────────────────
CREATE TABLE IF NOT EXISTS type_registry (
    name       VARCHAR(50) PRIMARY KEY,
    label      VARCHAR(100) NOT NULL,
    color      VARCHAR(20),
    sort_order INTEGER DEFAULT 0,
    enabled    BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);
INSERT INTO type_registry (name, label, color, sort_order) VALUES
  ('NOTE', '笔记', 'gray',   90),
  ('DISCOVERY', '发现', 'blue',  10),
  ('ARCH', '架构', 'blue',  20),
  ('DECISION', '决策', 'blue',  30),
  ('BUGFIX', '修复', 'red',   40),
  ('PREFERENCE', '偏好', 'purple', 50)
ON CONFLICT (name) DO NOTHING;

-- ── memories: add v2 columns ──────────────────────────────────────────────
ALTER TABLE memories ADD COLUMN IF NOT EXISTS stage      VARCHAR(12);
ALTER TABLE memories ADD COLUMN IF NOT EXISTS type       VARCHAR(50) NOT NULL DEFAULT 'NOTE';
ALTER TABLE memories ADD COLUMN IF NOT EXISTS scope      VARCHAR(10) NOT NULL DEFAULT 'global';
ALTER TABLE memories ADD COLUMN IF NOT EXISTS status     VARCHAR(12) NOT NULL DEFAULT 'active';
ALTER TABLE memories ADD COLUMN IF NOT EXISTS project_id UUID;

-- ── backfill stage from legacy layer (only while layer column still exists) ─
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memories' AND column_name = 'layer'
    ) THEN
        UPDATE memories SET stage = CASE
            WHEN layer = 'episodic' THEN 'observation'
            WHEN layer IN ('semantic', 'core') THEN 'memory'
            ELSE 'memory'
        END WHERE stage IS NULL;
    END IF;
END $$;

ALTER TABLE memories ALTER COLUMN stage SET NOT NULL;

-- CHECK / FK constraints wrapped in DO blocks: ADD CONSTRAINT has no IF NOT EXISTS
DO $$ BEGIN
    ALTER TABLE memories ADD CONSTRAINT memories_stage_chk  CHECK (stage IN ('observation', 'candidate', 'memory'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE memories ADD CONSTRAINT memories_scope_chk  CHECK (scope IN ('repo', 'global', 'user'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE memories ADD CONSTRAINT memories_status_chk CHECK (status IN ('active', 'archived', 'superseded'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE memories ADD CONSTRAINT memories_project_fk
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_memories_stage    ON memories(stage);
CREATE INDEX IF NOT EXISTS idx_memories_type     ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_status   ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_scope    ON memories(scope);
CREATE INDEX IF NOT EXISTS idx_memories_project  ON memories(project_id);
CREATE INDEX IF NOT EXISTS idx_memories_ns_stage ON memories(namespace, stage);
CREATE INDEX IF NOT EXISTS idx_memories_updated  ON memories(updated_at DESC);

-- ── hybrid_search v2 (multi-filter + pagination) ──────────────────────────
DROP FUNCTION IF EXISTS hybrid_search;
CREATE FUNCTION hybrid_search(
    query_text        TEXT,
    query_embedding   vector(1024),
    target_stage      VARCHAR DEFAULT NULL,
    target_types      TEXT[]  DEFAULT NULL,
    target_scopes     TEXT[]  DEFAULT NULL,
    target_statuses   TEXT[]  DEFAULT NULL,
    target_project    UUID    DEFAULT NULL,
    target_namespaces TEXT[]  DEFAULT NULL,
    match_limit       INT     DEFAULT 20,
    match_offset      INT     DEFAULT 0,
    rrf_k             INT     DEFAULT 60
) RETURNS TABLE (
    id UUID, content TEXT, summary TEXT, stage VARCHAR, type VARCHAR,
    scope VARCHAR, status VARCHAR, project_id UUID, source VARCHAR,
    importance SMALLINT, tags TEXT[], namespace VARCHAR,
    recall_count INTEGER, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
    rrf_score REAL
) AS $$
BEGIN
    RETURN QUERY
    WITH vector_results AS (
        SELECT m.id, row_number() OVER (ORDER BY m.embedding <=> query_embedding) AS rank
        FROM memories m
        WHERE m.embedding IS NOT NULL
          AND (target_stage      IS NULL OR m.stage = target_stage)
          AND (target_types      IS NULL OR m.type  = ANY(target_types))
          AND (target_scopes     IS NULL OR m.scope = ANY(target_scopes))
          AND (target_statuses   IS NULL OR m.status = ANY(target_statuses))
          AND (target_project    IS NULL OR m.project_id = target_project)
          AND (target_namespaces IS NULL OR m.namespace = ANY(target_namespaces))
    ),
    fts_results AS (
        SELECT m.id, row_number() OVER (
            ORDER BY ts_rank(m.fts, plainto_tsquery('simple', query_text)) DESC
        ) AS rank
        FROM memories m
        WHERE m.fts @@ plainto_tsquery('simple', query_text)
          AND (target_stage      IS NULL OR m.stage = target_stage)
          AND (target_types      IS NULL OR m.type  = ANY(target_types))
          AND (target_scopes     IS NULL OR m.scope = ANY(target_scopes))
          AND (target_statuses   IS NULL OR m.status = ANY(target_statuses))
          AND (target_project    IS NULL OR m.project_id = target_project)
          AND (target_namespaces IS NULL OR m.namespace = ANY(target_namespaces))
    ),
    combined AS (
        SELECT COALESCE(v.id, f.id) AS id,
               CAST(
                 (1.0 / (rrf_k + COALESCE(v.rank, 1000))) +
                 (1.0 / (rrf_k + COALESCE(f.rank, 1000)))
               AS REAL) AS rrf_score
        FROM vector_results v
        FULL OUTER JOIN fts_results f ON v.id = f.id
    )
    SELECT m.id, m.content, m.summary, m.stage, m.type,
           m.scope, m.status, m.project_id, m.source,
           m.importance, m.tags, m.namespace,
           m.recall_count, m.created_at, m.updated_at,
           c.rrf_score
    FROM combined c
    JOIN memories m ON m.id = c.id
    ORDER BY c.rrf_score DESC
    LIMIT match_limit OFFSET match_offset;
END;
$$ LANGUAGE plpgsql;

-- ── retire legacy layer ───────────────────────────────────────────────────
DROP INDEX IF EXISTS idx_memories_layer;
DROP INDEX IF EXISTS idx_memories_ns_layer;
ALTER TABLE memories DROP COLUMN IF EXISTS layer;
