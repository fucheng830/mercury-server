# Mercury Server ⚡

> Hermes 记忆系统服务端 — PostgreSQL + pgvector + A2A 协议。

**Mercury Server** 是 Hermes 长期记忆系统的云端后端。存储 AI 对话数据，生成向量嵌入，通过 [A2A 协议](https://a2a-protocol.org) 作为跨 Agent 记忆中间件。

## 特性

- **三层记忆模型** — 情景记忆（30天）→ 语义记忆（180天）→ 核心记忆（永久），自动晋升
- **混合搜索** — RRF（倒数排名融合）结合 pgvector 余弦相似度 + PostgreSQL 全文搜索
- **A2A 协议** — 7 个 skill（记忆和会话的增删搜共享），通过 Agent Card 自动发现
- **命名空间隔离** — 每个 Agent 私有存储，支持跨 Agent 共享
- **Client 接入 API** — REST 端点供 [Mercury](https://github.com/fucheng830/mercury) 客户端推送会话数据
- **知识图谱** — 自动实体/关系提取和图遍历
- **LLM 迭代引擎** — 每日复盘压缩、每周审查、每月知识整合（DeepSeek V4）

## 架构

```
                    ┌──────────────────────────────┐
                    │     外部 Agent                │
                    │  Claude / Gemini / 自定义     │
                    └──────────┬───────────────────┘
                               │ A2A 协议 (v1.0)
                               ▼
┌──────────────────────────────────────────────────────────┐
│                    Mercury Server                          │
│  ┌────────────┐ ┌─────────────┐ ┌────────────────────┐  │
│  │ REST API   │ │ A2A 协议层   │ │ MCP Server         │  │
│  │ /api/*     │ │ /.well-known│ │ hermes-memory MCP  │  │
│  └─────┬──────┘ └──────┬──────┘ └────────┬───────────┘  │
│        └───────────────┼─────────────────┘              │
│                        ▼                                │
│  ┌──────────────────────────────────────────────────┐   │
│  │          命名空间感知记忆服务                       │   │
│  └──────────────────────────────────────────────────┘   │
│                        ▼                                │
│           PostgreSQL 17 + pgvector 0.8                   │
└──────────────────────────────────────────────────────────┘
```

## 快速开始

```bash
# 克隆
git clone https://github.com/fucheng830/mercury-server.git
cd mercury-server

# 安装
pip install -r requirements.txt

# 配置 — 编辑 config.yaml 填入数据库和 LLM 配置
cp config.yaml config.local.yaml

# 启动
python server.py --port 8788 --shared
```

## A2A Agent Card

```bash
curl http://localhost:8788/.well-known/agent-card.json
```

任何支持 A2A 的 Agent 都能自动发现这 7 个 skill。

## A2A Skills

| Skill | 说明 |
|-------|------|
| `memory.write` | 写入记忆，指定层级、重要性、标签 |
| `memory.search` | 混合搜索（向量+FTS），跨命名空间 |
| `memory.read` | 分页读取记忆 |
| `memory.share` | 将记忆移至共享命名空间 |
| `session.search` | 按内容搜索 Claude Code 会话 |
| `session.read` | 读取会话元数据 |
| `session.share` | 共享会话给所有 Agent |

## 数据库

需要 PostgreSQL 17 + pgvector 扩展。完整建表语句见 `hermes/schema.sql`。

## 相关项目

- [Mercury](https://github.com/fucheng830/mercury) — 客户端守护进程
- [A2A 协议](https://a2a-protocol.org) — 开放 Agent-to-Agent 标准
- [pgvector](https://github.com/pgvector/pgvector) — PostgreSQL 向量搜索
