# Mercury 确定性反例门控（Counterexample Gate）设计

> 阶段 1 / 3 · mempal 设计合并系列
> 日期: 2026-07-02
> 状态: Draft（待 writing-plans）
> 前置: 记忆模型 v2（observation/candidate/memory）+ bi-temporal ledger（valid_from/valid_to/superseded_by）已上线

## 1. 动机

mercury 现有 supersede 机制靠 extractor ingest 时 LLM reconcile 判定矛盾：判定 `supersede` 时调 `supersede_memory(target)`，设 `status='superseded'`、关 `valid_to`、链 `superseded_by`。缺口：

- **无反例证据链**：supersede 只设 `superseded_by` 单指针（"谁取代了我"），没有"为什么被反驳"的多源证据累积。
- **无确定性门控**：降级完全靠 LLM 主观判定，无阈值；LLM 漏判的累积型矛盾（多条弱反例叠加）无法捕获。
- **无降级审计**：谁、何时、为何降级不可查（`sync_log` 是客户端同步日志，不是记忆降级审计）。

mempal 的 counterexample 机制（4 角证据 + 确定性 gate + append-only 事件审计）正好填补。本设计把反例理念合并进 mercury 现有 supersede + bi-temporal 基础，**补漏而非替换** LLM reconcile。

## 2. 目标 / 非目标

**目标**：
- 反例证据链接（多对多，支持 memory 或 session 片段作证据）
- 双通道降级：LLM reconcile（保留现有行为 + 加审计）+ 确定性阈值（补漏）
- 降级审计（append-only `memory_events`）
- 现有 581 条记忆 + LLM supersede 行为零回归

**非目标**（留给阶段 2/3）：
- agent 间消息总线（阶段 2）
- handoff→evidence→晋升闭环（阶段 3）
- knowledge card / tier（道/术/器）分层 —— mercury 暂不引入 mempal 的 tier，保留 stage+type 维度

## 3. 数据模型

### 3.1 `memory_refutations`（反例链接，append-only）

```sql
CREATE TABLE IF NOT EXISTS memory_refutations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  target_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  refuting_id UUID REFERENCES memories(id) ON DELETE SET NULL,
  session_ref JSONB,
  reason      TEXT NOT NULL,
  source      VARCHAR(20) NOT NULL,
  confidence  VARCHAR(10),
  namespace   VARCHAR(50) NOT NULL DEFAULT 'claude',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (refuting_id IS NOT NULL OR session_ref IS NOT NULL),
  UNIQUE(target_id, refuting_id, session_ref)
);
CREATE INDEX IF NOT EXISTS idx_refutations_target ON memory_refutations(target_id);
CREATE INDEX IF NOT EXISTS idx_refutations_ns_target ON memory_refutations(namespace, target_id);
```

- `target_id`：被反驳的 memory。
- `refuting_id` / `session_ref` 二选一（CHECK 约束）：反驳证据是另一条 memory，或 session 片段 `{session_id, span}`。
- `source` ∈ `{'llm_reconcile', 'agent', 'threshold'}`：反例来源。
- `confidence`：LLM 通道记 high/low；其他源 NULL。
- `UNIQUE(target_id, refuting_id, session_ref)`：防同一证据重复链接。

### 3.2 `memory_events`（降级审计，append-only）

```sql
CREATE TABLE IF NOT EXISTS memory_events (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_id  UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  event      VARCHAR(20) NOT NULL,
  trigger    VARCHAR(20) NOT NULL,
  reason     TEXT,
  details    JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memory_events_memory ON memory_events(memory_id, created_at DESC);
```

- `event` ∈ `{'refuted', 'demoted', 'superseded', 'restored'}`。
- `trigger` ∈ `{'llm_reconcile', 'threshold', 'manual'}`。
- `details`：快照，如 `{count, threshold, importance, type, refutation_ids: [...]}`。

### 3.3 和现有 `superseded_by` 的分工

| 列 | 语义 | 设置时机 |
|---|---|---|
| `memories.superseded_by`（现有） | 单指针继任者（"谁取代了我"，结果） | 通道 1 supersede 时 |
| `memories.valid_to`（现有） | 双时态有效区间终点 | supersede 时关闭 |
| `memory_refutations`（新增） | 多对多证据链（"为什么被反驳"，累积） | 任何通道建链时 |
| `memory_events`（新增） | 降级审计流水 | 每次状态变更 |

**互补不替换**：`superseded_by` 是结果指针，`refutations` 是证据累积。阈值通道降级时复用 `supersede_memory()`（自动管 valid_to + status），`superseded_by` 允许 NULL（累积反例降级未必有单一继任者）—— schema 里它本就是 nullable，无需改。

## 4. 行为：双通道降级

集成点：`hermes/extractor.py` reconcile 的 apply-actions 步骤（现有 `supersede_memory(tid)` 调用在 `extractor.py:179`）。

### 4.1 通道 1 · LLM reconcile（保留现有 + 加审计）

```
对 reconcile 判定的每个 supersede 动作 (new_mem → supersede target_tid):
  supersede_memory(target_tid, superseded_by=new_mem.id)   # 现有行为：status/valid_to/superseded_by
  INSERT memory_refutations(
    target_id=target_tid, refuting_id=new_mem.id,
    reason=<reconcile 给的矛盾理由>, source='llm_reconcile', confidence=<new_mem.confidence>)
  INSERT memory_events(target_tid, 'superseded', 'llm_reconcile',
                       details={superseded_by: new_mem.id, reason})
```

### 4.2 通道 2 · 确定性阈值补漏（新增）

通道 1 apply 后，对**本次涉及的 target + 新写入 memory** 批量重算 gate（不全量扫，避免性能开销）：

```
for m in (本次涉及的 target_ids + 本次 supersede 的发起方):
  if m.status != 'active': continue
  count = SELECT count(*) FROM memory_refutations
          WHERE target_id=m.id AND namespace=m.namespace
  thr = threshold(m.importance, m.type)
  if count >= thr:
    supersede_memory(m.id, superseded_by=NULL)     # 无单一继任者
    INSERT memory_events(m.id, 'demoted', 'threshold',
                         details={count, threshold=thr, importance, type, refutation_ids})
```

**两通道独立**：LLM 通道立即处理强语义矛盾（单条即可降），阈值通道补漏累积型矛盾（多条弱反例叠加）。LLM 通道已降级的，阈值通道因 `status!='active'` 跳过，不重复。

## 5. 阈值规则

```
threshold(importance, type):
  base = 2 if importance >= 4 else 1        # 高重要防误杀，普通 1 条即降
  if type == 'DECISION': base = max(base, 2)  # 决策类更谨慎
  return base
```

推荐初值（写入 `config.yaml` 可调，不硬编码）：`high_importance=4`, `threshold_high=2`, `threshold_normal=1`, `decision_min=2`。

## 6. API + MCP

REST（`server.py` 加路由）：
- `POST /api/memory/{id}/refute` — 事后加反例链。body: `{refuting_id?, session_ref?, reason, source='agent'}`。建链后立即重算 gate，达阈值则降级。返回 `{refuted: bool, gate: {count, threshold, demoted}}`。
- `POST /api/memory/{id}/demote` — 显式触发 gate 检查。不达阈值则 **拒绝**（HTTP 409 + reasons），达则降级。
- `GET /api/memory/{id}/refutations` — 列反例链 + 当前 gate 状态 `{count, threshold, active, refutations:[...]}`。
- `POST /api/memory/{id}/restore` — 撤销降级（status→active, 清 valid_to），写 `memory_events('restored')`。用于纠错。

MCP（`mercury-memory` server，可选，对齐 mempal 语义）：
- `memory_refute(target_id, refuting_id?, session_ref?, reason)`
- `memory_demote(target_id)` / `memory_refutations(target_id)`

## 7. 迁移（schema v3，幂等）

`hermes/schema.sql` 末尾追加（沿用现有 `IF NOT EXISTS` 幂等迁移风格，`init_db()` 启动自动执行）：

```sql
-- v3: counterexample gate (2026-07-02)
CREATE TABLE IF NOT EXISTS memory_refutations (...);   -- 见 3.1
CREATE TABLE IF NOT EXISTS memory_events (...);         -- 见 3.2
-- 不改 memories 表现有列（status 已有 'superseded'，superseded_by/valid_to 已存在）
```

现有 581 条记忆无 refutation → 阈值通道不影响 → 行为零变化。部署走现有链路（本地 commit → push → 0.17 pull → docker build → init_db 自动迁移）。

## 8. 测试

`tests/` 加 `test_counterexample_gate.py`（DB 集成，需 `MERCURY_TEST_DB=hermes_test`）：
- 建链 → 计数 → 达阈值降级（普通 importance=3，1 条即降）
- 高重要（importance=4）需 2 条才降级；DECISION 类需 2 条
- LLM 通道（通道 1）建 refutation + event，与阈值通道独立
- 阈值通道降级后 status='superseded' + valid_to 关 + memory_events 有 'demoted'/'threshold'
- namespace 隔离（A namespace 的反例不影响 B）
- `UNIQUE` 防重复链接
- `restore` 后 status='active' + event 'restored'
- **回归**：现有 LLM reconcile supersede 行为不破（extractor 现有测试通过）

## 9. 验收标准

1. 反例可建链（memory↔memory、memory↔session 片段两种形态）。
2. 通道 1：extractor supersede 时同步建 refutation + memory_events，superseded_by/valid_to 正确。
3. 通道 2：累积反例 ≥ 阈值时强制降级（即使 LLM 未判 supersede）。
4. 现有 LLM reconcile supersede 行为零回归（现有测试全绿）。
5. namespace 隔离正确。
6. `memory_events` 审计可查（每次降级有 trigger + details）。
7. `restore` 可撤销降级。
8. 迁移幂等（init_db 重复执行不报错）。

## 10. 风险 / 未决

- **阈值数值**：2/1/DECISION=2 是初值，需运行时观察误杀率调参（写入 config.yaml 可调）。
- **阈值通道扫描范围**：本设计只扫"本次 ingest 涉及的 target"（非全量），避免性能开销；副作用是历史 memory 若被事后 `POST /refute` 加链，需在 refute 端点内立即重算 gate（已设计）。
- **reason 质量**：通道 1 复用 reconcile LLM 给的矛盾理由；若理由为空，spec 实施时 fallback 到模板文案。
- **session_ref 片段定位**：依赖 sessions 表的 span 表示（实施时确认 sessions schema 是否已有可定位的偏移/行号字段；若无，第一阶段只支持 memory↔memory，session_ref 留 schema 占位待阶段 2 补）。
