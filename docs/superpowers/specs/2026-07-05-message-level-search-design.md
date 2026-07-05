# 消息级 FTS + 向量检索 设计

- **日期**: 2026-07-05
- **状态**: approved（用户口头确认，授权直接推进实现）
- **关联**: `docs/superpowers/specs/2026-06-18-memory-model-v2-design.md`（memory 模型 v2）

## 背景

当前 mercury-server 的检索只覆盖 `memories` 表（observation→candidate→memory 抽取层）。`sessions` 表只存会话级 `preview`（前 150 字符）+ 整体 embedding，**逐条对话消息从未落库**——用户无法搜索「过去某次会话里我 / Claude 说过什么」。

`providers/claude.py:25 _read_jsonl` 已能解析 Claude Code JSONL transcript 的完整 messages，但 ingest 时只抽了 preview（`claude.py:593`）和 message_count（`claude.py:614`）。

## 目标

**历史对话回溯搜索**：能搜到某次会话里的具体消息片段，并附带上下文窗口（前后各 N 条）。

## 非目标

- 不取代现有 memory 抽取层（两层并存，独立通道）
- 不索引 `tool_use` / `tool_result`（首版仅 user/assistant 文本，信噪比优先）
- 首版不做统一联邦搜索（schema 与函数签名预留接口）
- 前端 / MCP 不在首版（后端 + REST 先行）

## 架构决策

**渐进路线**：先上独立 `session_messages` 表 + 独立召回通道（方案 A），schema 与函数签名为方案 B（联邦）预留。

## 设计

### 1. Schema（`hermes/schema.sql`，幂等迁移，`init_db` 启动执行）

```sql
CREATE TABLE IF NOT EXISTS session_messages (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    namespace     VARCHAR(100) NOT NULL,
    seq           INTEGER NOT NULL,        -- 会话内序号(从 0),上下文窗口靠它
    role          VARCHAR(20) NOT NULL,    -- user / assistant
    content_text  TEXT NOT NULL,           -- 从 content blocks 抽出的纯文本
    embedding     vector(1024),
    fts tsvector GENERATED ALWAYS AS (to_tsvector('simple', content_text)) STORED,
    created_ts    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(session_id, seq)                -- 幂等 ingest 的关键
);
CREATE INDEX IF NOT EXISTS idx_session_messages_session ON session_messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_session_messages_ns      ON session_messages(namespace);
CREATE INDEX IF NOT EXISTS idx_session_messages_fts     ON session_messages USING gin(fts);
CREATE INDEX IF NOT EXISTS idx_session_messages_emb     ON session_messages USING hnsw (embedding vector_cosine_ops);
```

检索函数 `search_messages(...)`：仿 `hybrid_search` 针对 `session_messages` 表，vector（HNSW）+ FTS（GIN / 'simple'）双路 RRF（`rrf_k=60`）。**返回结构与 `hybrid_search` 同构**（都带 `rrf_score`），为方案 B 联邦预留。

### 2. 代码组织（遵守 500 行卫生规则）

`memory_service.py` 已 900+ 行超标。**新功能全部放新模块 `hermes/messages_service.py`**，`server.py` 只加薄路由。

### 3. Ingest 扩展

- `providers/claude.py` 新增 `_extract_text_turns(messages) → List[(seq, role, text, ts)]]`：只抽 `user` / `assistant` 的 `text` content block，跳过 `tool_use` / `tool_result`。
- `ingest_service.py` 在写完 `sessions` 行后，批量 embedding + 批量 `INSERT ... ON CONFLICT (session_id, seq) DO NOTHING`（重跑幂等）。

### 4. API + Service

- 新端点 `POST /api/sessions/search`，入参 `query / namespace / limit / offset / context_window=3`。
- 每条命中返回：`{message, session 元数据, prev[], next[]}`（用 `seq` 范围查上下文窗口——回溯体验的核心）。
- service 层放 `messages_service.py: search_messages(...)`。

### 5. 回灌策略

- **增量**：ingest 流程自动覆盖新会话。
- **历史**：`scripts/backfill_session_messages.py`，遍历已注册 clients 的 projects → `glob *.jsonl` → 逐会话解析入库，支持 `--project / --limit / --dry-run`，幂等可重跑。

### 6. 测试

- 单元：`_extract_text_turns` 正确跳过 tool 块。
- DB 集成：写 N 条消息 → `search_messages` 召回 + RRF 排序正确（沿用 `tests/test_memory_db.py` 风格，需 `MERCURY_TEST_DB`）。

## 预留联邦（方案 B，本次不实现）

`search_messages` 返回结构与 `hybrid_search` 同构（都带 `rrf_score`）。后续 B 方案只需 `UNION` 两函数结果再做一次 RRF——成本极低，因为数据形态已对齐。

## 验收标准

1. `python -m pytest tests/ -q` 全绿（含新 session_messages 测试）。
2. `MERCURY_TEST_DB=hermes_test python -m pytest tests/ -q` DB 集成通过。
3. 回灌脚本能对本地 Claude projects 解析入库，重跑幂等（行数不变）。
4. 端到端：`POST /api/sessions/search` 搜一个已知消息内容，能召回 + 返回上下文窗口。
