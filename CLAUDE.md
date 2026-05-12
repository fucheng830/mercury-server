# Mercury Server

Hermes 记忆系统服务端。PostgreSQL + pgvector 后端，提供 REST API、MCP、A2A 协议支持。

## 常用命令

```bash
# 启动服务
python server.py --port 8788 --shared

# 运行测试
python -m pytest tests/ -q
```

## 架构

```
server.py (FastAPI)
├── /api/*              REST API（Dashboard、History、Projects）
├── /api/memory/*       Memory CRUD + 混合搜索
├── /api/ingest/*       Client 数据接入
├── /api/a2a/*          A2A 协议端点
├── /.well-known/agent-card.json
├── hermes/
│   ├── db.py           PostgreSQL 连接池
│   ├── schema.sql      数据库表结构
│   ├── memory_service.py  记忆 CRUD + namespace 隔离
│   ├── a2a_service.py     A2A Agent Card + skill 路由
│   ├── ingest_service.py  Client 数据接入
│   ├── graph_service.py   实体/关系图谱
│   ├── iteration.py      LLM 迭代压缩引擎
│   └── embedding.py     bge-m3 向量嵌入
├── providers/          Claude/Codex 数据源适配
└── recap/              LLM 复盘生成引擎
```

## 数据模型

| 表 | 说明 |
|------|------|
| `memories` | 三层记忆（episodic/semantic/core）+ namespace 隔离 |
| `sessions` | Claude Code 会话元数据 + 向量 |
| `entities` / `relations` | 知识图谱 |
| `clients` | 注册的同步客户端 |
| `a2a_agents` | A2A Agent 注册信息 |

## A2A Skills

7 个 skills：`memory.write/search/read/share` + `session.search/read/share`
