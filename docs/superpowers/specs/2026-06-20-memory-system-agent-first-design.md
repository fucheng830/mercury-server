# Memory System Redesign — Agent-First Project Memory

> 日期: 2026-06-20 · 项目: mercury-server · 状态: 设计已确认，待实现
> 前置: 记忆模型 v2 (observation/candidate/memory) 已上线；本设计重构"记什么/怎么记/怎么用"

## 1. 目标与原则

把记忆系统从"完整记录发生的事"重构为"**召回驱动**"——只存将来召回能改变决策或省重复劳动的内容，并让 AI agent（Claude Code）下次进同一项目时**自动获得**这些记忆。

**三条原则：**
1. **召回驱动**：一条记忆的价值 = "将来召回它会不会改变一个决策/省掉重复工作"。不会 → 不存。
2. **agent 优先**：记忆的主要消费者是下次进同项目的 Claude Code agent，自动注入其上下文。
3. **项目级**：记忆按 project 隔离；agent 进项目只看该项目的记忆。

**只记 5 类持久知识**（type）：`DECISION`(决策+理由) / `ARCH`(约定·契约) / `BUGFIX`(坑+根因+解法) / `PREFERENCE`(稳定偏好) / `DISCOVERY`(关键事实·不变量)。过程日志、学习态（"掌握了…"）、泛描述、一次性意图 → **不吸入**。

## 2. 三个核心决策（已确认）

| 决策 | 选择 |
|------|------|
| 给谁 | AI agent（Claude Code），项目级，自动注入 |
| 捕获源 | session 末从**原始会话 JSONL**抽取（废弃 recap 作为记忆原料） |
| 召回机制 | **SessionStart 钩子**自动注入项目活跃记忆 |
| 阶段策略 | 高置信 → memory（可注入）；低置信 → candidate；**人工事后纠错**（非阻塞） |
| 历史数据 | 用新提取器**重抽历史会话**，旧噪声数据归档 |

## 3. 组件 ①：持久知识提取器（Capture）

**新建 `hermes/extractor.py`**，在 session ingest 时跑，取代 recap→observation→iteration 这条产记忆的链路。

- **输入**：一次 session 的原始消息（ingest API 已接收的会话内容；recap 引擎也读它）。
- **处理**：LLM 提取，**只**产出 5 类持久知识，每条带：
  - `content`（具体、带理由/根因/路径/数值，第三人称客观陈述）
  - `type`（5 类之一）
  - `importance`（1-5）
  - `confidence`（high/low，写时决策用，不入库）
  - `project_id`（session cwd → `get_or_create_project`）
  - `tags`
- **丢弃**：过程/学习态/泛描述/一次性意图（提取 prompt 显式 reject，参考已调优的 DEDUP_SYSTEM_PROMPT 思路，但这是**抽取**而非去重）。
- **写库**：
  - confidence=high → `stage=memory, status=active`（**可注入**）
  - confidence=low → `stage=candidate`（暂不注入，待人工确认或 auto_promote）
- **触发**：mercury CLI ingest session 时，服务端跑提取器（与 recap 生成并列；recap 作为复盘 UI 产物保留，**不再写 observation**）。

**废弃**：recap→observation 写入、iteration 的 observation→candidate 蒸馏（对新数据）。保留 `auto_promote`（高召回 candidate→memory）。

提取 prompt 要点（新 `EXTRACT_SYSTEM_PROMPT`）：
```
从这次开发会话中提取【将来再用得上的持久知识】，只这 5 类：
DECISION(决策+为什么) / ARCH(约定·契约·架构) / BUGFIX(坑+根因+解法) /
PREFERENCE(稳定偏好) / DISCOVERY(具体事实·数值·不变量)。
每条给 confidence: high(明确、可复用) / low(存疑、待确认)。
禁止提取：过程流水账、学习态("掌握了")、泛项目描述、一次性任务意图。
没有持久知识就返回空 processed。
输出 JSON: {"processed":[{"content","type","importance","confidence","tags"}]}
```

## 4. 组件 ②：SessionStart 自动注入（Recall）

**新接口 `GET /api/memory/recall`**：
- 参数：`project`（项目路径或名）、`namespace=claude`、`limit=30`、可选 `min_importance`
- 逻辑：解析 project→project_id（按 path/name **查**，查不到则返回空——recall 不建项目，项目只在 ingest/提取时由 `get_or_create_project` 创建），返回该项目 `stage=memory AND status=active` 的记忆，按 `importance DESC, updated_at DESC` 取 Top N。
- 响应：精简列表（id/type/importance/content 前 ~200 字/条）。

**Claude Code SessionStart 钩子**（用户提供 settings.json，我给配置）：
- 钩子脚本：取当前 cwd（项目路径）→ `curl /api/memory/recall?project=<cwd>` → 把结果格式化为上下文块输出。
- 注入格式（示例）：
  ```
  ## 本项目记忆（agent 自动注入，按需遵守/参考）
  - [DECISION★5] social-auto-upload 作为子模块经 CLI 集成，不直接嵌依赖（降低版本耦合）
  - [GOTCHA★4] 代理管理批量导入曾因并发丢数据，已加分布式锁
  - [ARCH★4] 万象运营是 Python3.12 FastAPI monorepo
  ...
  ```
- 上限 ~30 条高重要性，控上下文成本。agent 可在会话中忽略或深挖。

## 5. 组件 ③：模型调整

- **project 绑定**：提取器对每条记忆写 project_id（cwd→project）。recall 按 project 过滤。
- **阶段策略（agent 优先）**：提取器高置信直进 `memory`（可注入）；低置信进 `candidate`（不注入）。人工审查改为**事后纠错**：在 UI 把坏记忆降级（`status=archived`），不再阻塞全部记忆可用。
- **type**：5 类（DECISION/ARCH/BUGFIX/PREFERENCE/DISCOVERY），沿用 type_registry。
- **不改 schema**：confidence 是写时决策（映射到 stage），不新增列。

## 6. 组件 ④：历史数据重抽

- 用新提取器对**历史原始会话**（`~/.claude/projects/*/*.jsonl`，mercury CLI 已知）批量重抽 → 干净项目记忆。
- 分批跑（按项目/按时间窗），类似 backfill，走 gpt-5.5。
- 重抽后：旧的 724 observation + 181 candidate（recap/旧 prompt 产物）**归档**（`status=archived` 或清理），不注入。
- 跑前对相关记忆延后过期/保护，避免清理误伤。

## 7. 不做（后续阶段）

- MCP `memory_recall` 按需会话中召回（Q3 的 B 选项）。
- 超越"重要性"的相关性排序（向量召回排序）。
- SP3（候选合并/批量）与 SP4（活动/运维/图谱增强）UI。

## 8. 实现阶段

1. **Phase 1 — Capture**：`hermes/extractor.py` + EXTRACT_SYSTEM_PROMPT + 接入 ingest + 废弃 recap→observation。新会话产出干净记忆。
2. **Phase 2 — Recall**：`/api/memory/recall` + SessionStart 钩子配置。agent 进项目自动注入。
3. **Phase 3 — 历史重抽**：批量重抽历史会话 + 归档旧数据。

## 9. 验证

- 提取器：对几个真实 session 跑，人工抽查产出是否为 5 类持久知识、是否丢弃噪声、confidence 判断是否合理。
- recall：钩子在测试项目触发，确认上下文块正确注入、条数受控。
- 端到端：在 mercury-server 项目自身装钩子，验证"下次进项目 agent 看到该项目记忆"。
