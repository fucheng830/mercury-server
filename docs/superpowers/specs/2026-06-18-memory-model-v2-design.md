# Mercury Memory Model v2 — 设计文档 (SP1)

> 日期: 2026-06-18 · 项目: mercury-server (Hermes 记忆后端) · 子项目: SP1 / 共 4 个
> 状态: 设计已确认，待实现
> 参考 UI: `claude-history-recap` 前端的记忆管理工作台（截图目标设计）

## 1. 目标与范围

把现有"三层记忆 (episodic/semantic/core)"模型重构为截图目标设计的"**观察→候选→记忆** 工作流模型"，新增 `type / scope / status / project` 维度，使后端数据模型与目标 UI 1:1 对齐。本子项目只做**后端模型 + API + 数据迁移**；前端重做属于 SP2，不在本 spec 范围。

**范围外**: 前端 UI (SP2)、候选/观察交互工作流 UI (SP3)、知识图谱/活动/运维增强 (SP4)。

## 2. 已确认决策

| # | 决策点 | 选择 |
|---|--------|------|
| Q1 | 生命周期轴 | **C**: 用 `stage`(observation/candidate/memory) **取代** `layer`(episodic/semantic/core) |
| Q2 | 阶段流转触发 | **B**: session→observation(自动提取) → observation→candidate(`iteration.py` 自动 LLM 提炼) → candidate→memory(**人工确认**，可选高置信自动旁路) |
| Q3 | type 轴 | **C+iii**: 可扩展枚举 (`type_registry` 表) + LLM 提取时预填、人工确认关口可改 |
| Q4 | scope/project/namespace | **A**: 保留 namespace(隔离) + 新增 `scope`(repo/global/user) + 新建 `projects` 表 + memory 关联 project |
| Q5 | status | **B+ii**: `active/archived/superseded` 三态，全阶段通用 |
| Q6 | 迁移 + TTL | **b+i**: episodic→observation、semantic/core→memory(grandfather)、TTL observation 30d/candidate 90d/memory 永久 |
| 方案 | schema 结构 | **①**: 单 `memories` 表 + `stage` 列（沿用现有单表 + 图谱链接 + hybrid_search 架构） |

## 3. 目标 Schema

### 3.1 `memories` 表 (v2)

```sql
-- 新增列（幂等 ALTER，追加在 schema.sql 末尾的 v2 迁移段）
ALTER TABLE memories ADD COLUMN IF NOT EXISTS stage      VARCHAR(12);
ALTER TABLE memories ADD COLUMN IF NOT EXISTS type       VARCHAR(50) NOT NULL DEFAULT 'NOTE';
ALTER TABLE memories ADD COLUMN IF NOT EXISTS scope      VARCHAR(10) NOT NULL DEFAULT 'global';
ALTER TABLE memories ADD COLUMN IF NOT EXISTS status     VARCHAR(12) NOT NULL DEFAULT 'active';
ALTER TABLE memories ADD COLUMN IF NOT EXISTS project_id UUID;

-- 数据回填 stage（见 §4 迁移）之后：
ALTER TABLE memories ALTER COLUMN stage SET NOT NULL;

-- CHECK 约束必须用 DO 块包裹：PG 的 ADD CONSTRAINT 不支持 IF NOT EXISTS，
-- 而 init_db() 每次启动都重跑 schema.sql，裸 ADD CONSTRAINT 二次启动必报错。
DO $$ BEGIN
  ALTER TABLE memories ADD CONSTRAINT memories_stage_chk  CHECK (stage IN ('observation','candidate','memory'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE memories ADD CONSTRAINT memories_scope_chk  CHECK (scope IN ('repo','global','user'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE memories ADD CONSTRAINT memories_status_chk CHECK (status IN ('active','archived','superseded'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- project 外键（同样用 DO 块保证幂等）+ 新索引
DO $$ BEGIN
  ALTER TABLE memories ADD CONSTRAINT memories_project_fk
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_memories_stage      ON memories(stage);
CREATE INDEX IF NOT EXISTS idx_memories_type       ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_status     ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_scope      ON memories(scope);
CREATE INDEX IF NOT EXISTS idx_memories_project    ON memories(project_id);
CREATE INDEX IF NOT EXISTS idx_memories_ns_stage   ON memories(namespace, stage);
CREATE INDEX IF NOT EXISTS idx_memories_updated    ON memories(updated_at DESC);

-- 废弃旧 layer 索引/列（迁移完成后，见 §4）
DROP INDEX IF EXISTS idx_memories_layer;
DROP INDEX IF EXISTS idx_memories_ns_layer;
ALTER TABLE memories DROP COLUMN IF EXISTS layer;
```

保留列: `id, content, summary, source, importance(1-5), tags[], embedding, fts, recall_count, namespace, created_at, expires_at, updated_at`。

### 3.2 `projects` 表（新建）

```sql
CREATE TABLE IF NOT EXISTS projects (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       VARCHAR(200) NOT NULL,
    path       TEXT,
    namespace  VARCHAR(50) NOT NULL DEFAULT 'claude',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(namespace, name)
);
```

### 3.3 `type_registry` 表（新建，可扩展枚举）

```sql
CREATE TABLE IF NOT EXISTS type_registry (
    name       VARCHAR(50) PRIMARY KEY,
    label      VARCHAR(100) NOT NULL,
    color      VARCHAR(20),        -- 前端徽章配色：blue/red/purple/amber/...
    sort_order INTEGER DEFAULT 0,
    enabled    BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 种子（幂等）
INSERT INTO type_registry (name, label, color, sort_order) VALUES
  ('NOTE',      '笔记',     'gray',  90),
  ('DISCOVERY', '发现',     'blue',  10),
  ('ARCH',      '架构',     'blue',  20),
  ('DECISION',  '决策',     'blue',  30),
  ('BUGFIX',    '修复',     'red',   40),
  ('PREFERENCE','偏好',     'purple',50)
ON CONFLICT (name) DO NOTHING;
```

### 3.4 `hybrid_search` v2

替换现有函数，参数从 `target_layer` 改为多维过滤 + 分页：

```sql
CREATE OR REPLACE FUNCTION hybrid_search(
    query_text       TEXT,
    query_embedding  vector(1024),
    target_stage     VARCHAR DEFAULT NULL,
    target_types     TEXT[]  DEFAULT NULL,
    target_scopes    TEXT[]  DEFAULT NULL,
    target_statuses  TEXT[]  DEFAULT NULL,
    target_project   UUID    DEFAULT NULL,
    target_namespaces TEXT[] DEFAULT NULL,
    match_limit      INT DEFAULT 20,
    match_offset     INT DEFAULT 0,
    rrf_k            INT DEFAULT 60
) RETURNS TABLE (
    id UUID, content TEXT, summary TEXT, stage VARCHAR, type VARCHAR,
    scope VARCHAR, status VARCHAR, project_id UUID, source VARCHAR,
    importance SMALLINT, tags TEXT[], namespace VARCHAR,
    recall_count INTEGER, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
    rrf_score REAL, total_count BIGINT
) AS $$ ... $$ LANGUAGE plpgsql;
```

过滤条件：`stage / type / scope / status / project_id / namespace`，按 `rrf_score DESC` 排序，支持 `limit/offset` 分页并返回 `total_count`。

## 4. 数据迁移（幂等，由 `init_db()` 在服务器启动时执行）

`schema.sql` 末尾追加 v2 迁移段，顺序执行、全部幂等：

```sql
-- 1) 建 projects / type_registry（§3.2 §3.3）
-- 2) 加 stage/type/scope/status/project_id 列
-- 3) 回填 stage（仅在 stage IS NULL 时）
UPDATE memories SET stage = CASE
    WHEN layer = 'episodic' THEN 'observation'
    WHEN layer = 'semantic' THEN 'memory'   -- grandfather
    WHEN layer = 'core'     THEN 'memory'
    ELSE 'memory'
END WHERE stage IS NULL;
-- 4) 迁移默认值（type=NOTE/scope=global/status=ACTIVE 已由列默认值覆盖）
--    project 推断：留空（NULL），后续由 ingest 从 session.project 关联
-- 5) stage SET NOT NULL + CHECK + 其余约束/索引（§3.1）
-- 6) CREATE OR REPLACE hybrid_search v2（§3.4）
-- 7) DROP 旧 layer 索引 + DROP COLUMN layer（最后一步）
```

**执行方式**: 不在本地跑。代码合并 → push → 服务器 `git pull` → 重启服务（`init_db()` 自动执行幂等迁移）。**不可逆点：DROP COLUMN layer**——上线前需 DB 备份（0.17 已有每日 03:00 pg_dump，见记忆库记录）。

**回填映射**（Q6=b）:
- `episodic` → `observation`
- `semantic` → `memory`（grandfather，视为已确认）
- `core` → `memory`

## 5. 服务层改动 (`hermes/`)

| 文件 | 改动 |
|------|------|
| `memory_service.py` | `LAYER_TTL_DAYS`→`STAGE_TTL_DAYS`={observation:30,candidate:90,memory:None}；`write_memory` 参数 `layer`→`stage` + 新增 `type/scope/status/project_id`；`read_memories`/`search_memories` 过滤维度扩展；`promote_memory(target_layer)` → `confirm_candidate(memory_id)`(candidate→memory) + `distill_to_candidate()`(observation→candidate)；`auto_promote` 改为高置信 candidate→memory；`get_memory_stats` 按 stage 统计；新增 `list_memories(filters, page, size)` 多维筛选+分页；新增 type_registry CRUD |
| `iteration.py` | LLM 提炼目标：episodic→semantic 改为 **observation→candidate**（去重/压缩产出 candidate，等待人工确认）；DEDUP_SYSTEM_PROMPT 文案相应更新 |
| `bridge.py` / `ingest_service.py` / `a2a_service.py` / `server_recap.py` / `graph_service.py` / `migration.py` | 所有 `layer` 引用 → `stage`（84 处，按文件逐一更新） |

## 6. API 改动 (`server.py`)

**修改**（参数 layer→stage，响应增 type/scope/status/project）:
- `POST /api/memory/query`、`GET /api/memory/stats`、`GET /api/memory/recent`、`GET /api/memory/graph`、`GET /api/memory/read`、`POST /api/memory/write`、`POST /api/memory/search`、`DELETE /api/memory/delete`

**新增**:
- `GET /api/memory/list` — 多维筛选(type/project/scope/status/stage)+排序+分页(默认25/页)，对应截图"All memories"主列表
- `POST /api/memory/:id/confirm` — 候选→记忆人工确认（可同时改 type/scope/project）
- `POST /api/memory/:id/reject` — 候选驳回 → status=archived
- `GET /api/memory/candidates` / `GET /api/memory/observations` — 侧栏计数+列表
- `GET/POST/PUT/DELETE /api/memory/types` — type_registry CRUD
- `GET/POST /api/memory/projects` — projects CRUD

## 7. 配置 (`config.yaml`)

新增 stage TTL 配置（覆盖代码默认值）：
```yaml
hermes:
  memory:
    stage_ttl_days:
      observation: 30
      candidate: 90
      memory: null
```

## 8. 验证

- `python -m pytest tests/` 现有用例需同步更新（layer→stage）
- 新增用例：stage 迁移正确性、多维筛选+分页、candidate 确认/驳回流转、type_registry 扩展
- 迁移幂等性：连续两次 `init_db()` 不报错、数据不变

## 9. 风险

- **DROP COLUMN layer 不可逆** → 上线前确认 0.17 已有 pg_dump 备份
- **84 处 layer 引用**跨 9 文件，遗漏会导致运行时错误 → 用 grep 全量覆盖 + py_compile + pytest 兜底
- **hybrid_search 签名变更**影响所有调用方 → 同步更新 memory_service 调用
