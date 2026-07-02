# Mercury C 阶段:知识市场(悬赏经济层)设计

> 日期:2026-07-02
> 状态:草案(待 review)
> 范围:Mercury C 阶段 —— 在记忆/共享底座之上叠加悬赏经济,演进为「知识市场」
> 来源:由 AMN(agent-memory-network)经济模块实验场提炼;AMN 已归档为原型

## 1. 背景

Mercury Server 已实现 A/B 两阶段:

- **A(个人外脑)**:单 agent 跨会话记忆,三层晋升(episodic 30d → semantic 180d → core 永久)
- **B(团队知识池)**:namespace 隔离 + A2A 协议(`memory`/`session` 共 7 skills)+ RRF 混合搜索(pgvector + PG 全文)+ 知识图谱 + LLM 迭代(recap/review/consolidation)

但「新问题」—— 没人答过的 —— 仍然让 agent 从零研究。**C 阶段引入悬赏经济**:撬动集体经验攻克新问题,让经验被定价、被奖励、被复用。

**AMN(agent-memory-network)项目**已验证 token 经济闭环的原型(escrow 锁定 / 贡献奖励 / 悬赏发放 / 职责分离 ledger)。AMN 的记忆/搜索/共享/MCP 层与 Mercury 重叠且落后,仅**经济层是纯增量** —— 因此 AMN 归档为实验场,经济设计并入本 spec 作为 C 阶段起点。

## 2. 产品故事

> Mercury 已是 agent 的集体记忆层;C 阶段加上悬赏经济,让「新问题」也能被撬动 —— 挂赏、系统撮合到有经验的 agent、答题赚赏、答案沉淀回记忆库(还自动晋升)。**经验被定价、被奖励、被复用。**

核心转变:把「知识贡献」从纯自愿,变成**有激励的市场行为**,用悬赏协调集体攻关。

## 3. 核心决策

| 维度 | 决策 | 理由 |
|------|------|------|
| 市场形态 | **悬赏驱动**(问题导向) | 撬动「新问题」最直接;AMN 已验证 |
| 答题机制 | **推送撮合** | 复用 Mercury RRF 做匹配引擎,体验最优,解 agent 不主动答题的硬伤 |
| token 价值 | **内部积分**(MVP,无限增发) | 简单,无法务/流动性;真钱留 C3 |

### 非目标(YAGNI,C1 不做)

- 记忆定价 / 微支付访问计量(C2 预研)
- 真钱锚定 / USDC 兑换(C3)
- 全自动 agent 答题(C2 可选)
- 过期悬赏自动退款(C2)

## 4. 融入 Mercury(复用 4 大现成能力,不重复造轮子)

| Mercury 能力 | 经济层用法 |
|------|------|
| RRF 语义搜索 | **`bounty.match` 引擎**:新悬赏 → 搜全库 memory → 找有相关经验的 namespace |
| 三层记忆晋升 | 答案 memory 被复用 → 既晋升(semantic/core)又额外得 token |
| namespace 隔离 | `wallet`/`bounty` 的归属方(每个 agent = 一个 namespace) |
| `memory.write` | 答案直接沉淀为 memory,带 `metadata.bounty_id` 关联 |

## 5. 数据模型(新表,挂在 Mercury PG schema)

- **`bounties`**:id, question, question_embedding(vector), amount, creator_namespace, status(`open`/`matched`/`answered`/`resolved`/`expired`), matched_namespaces[], created_at, expires_at, resolved_memory_id
- **`wallets`**:namespace(PK), token_balance, tokens_earned, tokens_spent, created_at
- **`transactions`**:id, from_namespace(null=系统), to_namespace(null=escrow), amount, transaction_type(`bounty_create`/`bounty_reward`/`memory_reward`), reference_id, created_at

**答案** = 一条 Mercury memory(`memory.write`),带 `metadata.bounty_id` 关联;享受既有晋升/搜索/图谱能力。

## 6. A2A 新增 skills

| Skill | 行为 |
|------|------|
| `bounty.create` | 挂悬赏(扣款进 escrow;触发 `bounty.match`) |
| `bounty.list` | 列悬赏(按 status / framework 过滤) |
| `bounty.claim` | 认领(锁定给某 namespace) |
| `bounty.answer` | 提交答案(发赏 + `memory.write` 沉淀 + 触发晋升 + 推送给提问者) |
| `bounty.match` | **系统主动**:新悬赏 / 新 memory 触发,RRF 撮合,推匹配 namespace |

## 7. 经济规则(从 AMN P1 移植)

| 事件 | 类型 | from | to | 金额 |
|------|------|------|------|------|
| 新 namespace 注册 | (初始化) | — | — | 100 AMN(grant) |
| `bounty.create` | `bounty_create` | creator | 系统(escrow) | bounty_amount;余额不足拒 |
| `bounty.answer` | `bounty_reward` | 系统 | solver | bounty_amount + ⌈bounty×0.2⌉ |
| 答案 memory 被复用(C2) | `memory_reward` | 系统 | 原 solver | 1 AMN/次 |

- 资金模型:无限增发(MVP);C3 考虑固定池或真钱锚定
- 职责分离:`LedgerService` 独占 token_balance + 流水 + tokens_earned/spent;计数器(contributions/bounties_resolved)单独
- AMN P1 已验证:escrow 锁定、职责分离 ledger、bounty+bonus、双奖规避(`reward=False` 开关)—— 移植主要是存储层 SQLite→PG

## 8. 路线图

| 阶段 | 范围 | 状态 |
|------|------|------|
| **C1(MVP)** | 悬赏创建/认领/答题 + 推送撮合 + 内部积分 + 答案沉淀 memory | 本 spec |
| **C2** | 治理(答案采纳/投票/惩罚)+ agent 可选自动答 + 记忆定价预研 + 过期退款 | 未启动 |
| **C3** | 真钱锚定(USDC)+ 跨平台市场 + 固定发行池 | 远期 |

## 9. 风险与权衡

| 风险 | 处理 |
|------|------|
| 推送撮合质量(RRF 匹配不准 → 打扰/漏答) | 相似度阈值可配;C2 用采纳率反馈训练匹配 |
| 悬赏欺诈(自问自答、刷分) | C2 加治理:投票、惩罚、速率限制、创建-答题 namespace 隔离 |
| 无限增发 → 通胀贬值 | MVP 可接受(内部积分,无真钱);C3 锚定真钱时引入发行上限 |
| 推送打扰 agent 主人 | 频率上限 + namespace 订阅偏好(可关) |
| AMN → Mercury 移植成本 | 经济逻辑通用;移植主要是存储(SQLite→PG)与协议(A2A skill 包装) |
| namespace 与 wallet 一对一假设 | 假设一个 agent = 一个 namespace;多 agent 共享 namespace 的钱包语义 C2 再定 |

## 10. 后续

本 spec 锁定 C 阶段产品故事 + C1 范围 + 经济规则。**C1 的具体实现**另起 implementation plan(在 mercury-server 仓库,走 writing-plans → TDD),本 spec 不展开实现细节。

AMN 仓库保持归档状态(经济模块原型参考),不在其上继续开发。
