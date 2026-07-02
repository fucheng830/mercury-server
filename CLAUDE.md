# Mercury Server

Hermes 记忆系统——**全栈仓库**（FastAPI 后端 + Vue3 前端源码 + 构建产物）。PostgreSQL + pgvector 后端，提供 REST API、MCP、A2A 协议；前端为记忆工作台 UI。

## 常用命令

```bash
# 启动后端服务
python server.py --port 8788 --shared

# 后端测试
python -m pytest tests/ -q                      # 纯逻辑测试（无需 DB）
MERCURY_TEST_DB=hermes_test python -m pytest tests/ -q   # 含 DB 集成（需一次性建库+vector扩展）

# 前端开发（web/，端口 5180，代理 /api → 本地 8788）
cd web && npm run dev

# 前端构建（产出 web/dist，被后端 serve；dist 已纳入 git 正常跟踪）
cd web && npm run build
```

## 架构

```
server.py (FastAPI, 端口 8788)
├── /api/*              REST API（Dashboard、History、Projects）
├── /api/memory/*       Memory CRUD + 混合搜索 + 候选审查 + types/projects
├── /api/ingest/*       Client 数据接入
├── /api/a2a/*          A2A 协议端点
├── /.well-known/agent-card.json
├── hermes/
│   ├── db.py           PostgreSQL 连接池（含重试/锁）
│   ├── schema.sql      数据库表结构 + v2 幂等迁移（init_db 每次启动执行）
│   ├── memory_service.py  记忆 CRUD + namespace 隔离 + 多维筛选/分页 + stage 流转
│   ├── a2a_service.py     A2A Agent Card + skill 路由
│   ├── ingest_service.py  Client 数据接入
│   ├── graph_service.py   实体/关系图谱
│   ├── iteration.py      observation→candidate LLM 提炼引擎
│   └── embedding.py     bge-m3 向量嵌入
├── providers/          Claude/Codex 数据源适配
├── recap/              LLM 复盘生成引擎
└── web/                ← Vue3 前端（源码 + 产物）
    ├── src/            源码：App.vue(工作台 shell) / views(MemoriesView…) / components/memory / composables/useMemoryApi
    ├── public/         favicon.svg（六边形-M logo）
    ├── index.html, package.json, vite.config.js (代理 → 8788)
    └── dist/           构建产物（后端 StaticFiles serve；.dockerignore 排除 src，镜像只装 dist）
```

## 数据模型

记忆模型 v2：`observation → candidate → memory` 工作流 + `type/scope/status/project` 维度。
设计文档：`docs/superpowers/specs/2026-06-18-memory-model-v2-design.md` · 设计稿：`../claude-history-recap/docs/design/`（历史，前端源码已迁入本仓库 web/）

| 表 | 说明 |
|------|------|
| `memories` | 记忆（stage: observation/candidate/memory + type/scope/status/project_id）+ namespace 隔离 |
| `type_registry` | 可扩展记忆类型枚举（NOTE/DISCOVERY/ARCH/DECISION/BUGFIX/PREFERENCE/PROCEDURE/SESSION） |
| `projects` | 项目（id/name/path/namespace），memory 可关联 |
| `sessions` | Claude Code 会话元数据 + 向量 |
| `entities` / `relations` | 知识图谱 |
| `clients` | 注册的同步客户端 |
| `a2a_agents` | A2A Agent 注册信息 |
| `memory_refutations` / `memory_events` | 反例证据链 + 降级审计（counterexample gate，确定性阈值补漏 supersede）|

## 部署（0.17）

部署目录 `/home/ubuntu/mercury-server-docker/`（docker-compose，端口 8788，挂载 hermes/recap/providers/server.py/config.yaml，web/dist 烤进镜像）。流程：本地 `cd web && npm run build` → `git add -A && git commit && git push` → 0.17 `git pull && docker build -t mercury-server-docker-mercury-server:latest . && docker compose up -d mercury-server`。迁移在容器启动时由 `init_db()` 自动执行（幂等）。数据库每日 03:00 pg_dump 备份。

## A2A Skills

7 个 skills：`memory.write/search/read/share` + `session.search/read/share`
