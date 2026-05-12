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

CREATE OR REPLACE FUNCTION hybrid_search(
    query_text TEXT,
    query_embedding vector(1024),
    target_layer VARCHAR DEFAULT NULL,
    match_limit INT DEFAULT 20,
    rrf_k INT DEFAULT 60,
    target_namespaces TEXT[] DEFAULT NULL
)
RETURNS TABLE (
    id UUID, content TEXT, summary TEXT, layer VARCHAR,
    source VARCHAR, importance SMALLINT, tags TEXT[],
    namespace VARCHAR,
    recall_count INTEGER, created_at TIMESTAMPTZ,
    rrf_score REAL
) AS $$
BEGIN
    RETURN QUERY
    WITH vector_results AS (
        SELECT m.id, row_number() OVER (ORDER BY m.embedding <=> query_embedding) AS rank
        FROM memories m
        WHERE m.embedding IS NOT NULL
          AND (target_layer IS NULL OR m.layer = target_layer)
          AND (target_namespaces IS NULL OR m.namespace = ANY(target_namespaces))
    ),
    fts_results AS (
        SELECT m.id, row_number() OVER (
            ORDER BY ts_rank(m.fts, plainto_tsquery('simple', query_text)) DESC
        ) AS rank
        FROM memories m
        WHERE m.fts @@ plainto_tsquery('simple', query_text)
          AND (target_layer IS NULL OR m.layer = target_layer)
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
    SELECT m.id, m.content, m.summary, m.layer,
           m.source, m.importance, m.tags,
           m.namespace,
           m.recall_count, m.created_at,
           c.rrf_score
    FROM combined c
    JOIN memories m ON m.id = c.id
    ORDER BY c.rrf_score DESC
    LIMIT match_limit;
END;
$$ LANGUAGE plpgsql;

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
