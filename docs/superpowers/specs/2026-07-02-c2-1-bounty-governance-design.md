# Mercury C2-1:悬赏治理(提问者采纳制)设计

> 日期:2026-07-02
> 状态:草案(待 review)
> 范围:C2 第一阶段 —— 悬赏答案治理(提问者采纳/拒绝),改造 C1 的 answer 流程
> 前置:C1(bounty 经济核心)已交付,分支 `feat/c1-bounty-economy`

## 1. 背景

C1 的 `answer_bounty` **立即发赏 + resolved** —— 无质量把关,任何答题都拿钱,易被低质/刷分灌爆。

C2-1 引入**提问者采纳制**:答案先 `pending`,提问者判定 → 采纳才发赏 + memory 晋升;拒绝则不发赏、bounty 重开。

## 2. 设计决策

| 维度 | 决策 |
|------|------|
| 采纳机制 | **提问者采纳**(提问者评判,简单) |
| reject 行为 | solver 白干(不发赏,无额外扣),bounty 回 `open` 可重认领 |
| memory 晋升 | 采纳 → observation→memory(复用 Mercury 三层);拒绝 → memory `archived` |
| 惩罚 | MVP 无(白干即足够);C3 可加恶意答案扣分 |

### 非目标(C2-1 不做)
- 过期自动退款(C2-2,本设计的 `expired` 状态留接口)
- 投票 / 社区治理(C3)
- 自动答(C2-3)

## 3. 流程改造(C1 → C2-1)

| 步骤 | C1 | C2-1 |
|------|----|------|
| create / claim | escrow / 认领 | 同 |
| **answer** | 立即发赏 + resolved | → `answered`(pending),不发赏,solution 存 memory(stage=`observation`) |
| **accept**(新) | — | 提问者采纳 → 发赏(bounty+bonus)+ memory 晋升(`observation`→`memory`)+ `resolved` |
| **reject**(新) | — | 提问者拒绝 → 不发赏,bounty 回 `open`(可重认领),solution memory 标 `archived` |

## 4. 状态机

```
open → claimed → answered → accepted(=resolved)
                          → rejected(→ open,可重认领)
                          → expired(C2-2 衔接)
```

新增 status:`answered`(C1 只有 open/claimed/resolved/expired)。

## 5. 数据模型改动

`bounties` 表:
- `status` CHECK 加 `'answered'`
- 新增列:`accepted_at TIMESTAMPTZ`、`rejected_at TIMESTAMPTZ`
- 复用 `resolved_memory_id`(accept 时回填;reject 时 memory 改 `archived`)

## 6. Service 改动(`bounty_service.py`)

| 函数 | 改动 |
|------|------|
| `answer_bounty` | **改**:不发赏,solution 存 memory(stage=`observation`),bounty → `answered`。返回 `{bounty_id, memory_id, status: "answered"}` |
| `accept_bounty`(新) | 验证 caller=creator + status=`answered` → 发赏(bounty+bonus)+ memory 晋升 → `resolved` + `accepted_at` |
| `reject_bounty`(新) | 验证 caller=creator + status=`answered` → 不发赏,memory 改 `archived`,bounty 回 `open`(`claimer` 清空)+ `rejected_at` |

## 7. A2A + REST

新增 skills / endpoints:
- `bounty.accept` / `POST /api/bounty/{id}/accept`
- `bounty.reject` / `POST /api/bounty/{id}/reject`

agent card skills 加 2 项。

## 8. 测试

| 测试 | 改动 |
|------|------|
| `test_claim_and_answer_reward`(C1) | **调整**:answer 不再发赏;改走 accept 后发赏 |
| `test_economy_roundtrip_bookkeeping`(C1) | **调整**:加 accept 步骤 |
| `test_answer_enters_pending`(新) | answer 后 status=`answered`,不发赏,solver 余额不变 |
| `test_accept_rewards_and_promotes`(新) | accept → 发赏 + memory stage=`memory` |
| `test_accept_only_creator`(新) | 非 creator accept → 拒绝 |
| `test_reject_reopens_bounty`(新) | reject → status 回 `open`,claimer 清空,可重 claim |

预期:C1 测试调整后仍绿 + 4 个新治理测试。

## 9. 风险

| 风险 | 处理 |
|------|------|
| memory 晋升需更新 stage(observation→memory) | 复用 memory_service 的 stage 流转(或 UPDATE 直接改) |
| reject 后 bounty 重开,原 solver 再答? | 允许(claim 开放);C3 可加冷却 |
| accept 超时无机制 | 留 `expired` 接口,C2-2 实现 cron |
