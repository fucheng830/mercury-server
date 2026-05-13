# Mercury Server ⚡

> The Hermes memory system backend — PostgreSQL + pgvector + A2A protocol.

**Mercury Server** is the cloud backend for the Hermes long-term memory system. It stores AI conversation data, generates vector embeddings, and serves as a cross-agent memory middleware via the [A2A protocol](https://a2a-protocol.org).

## Features

- **Three-layer memory** — episodic (30d) → semantic (180d) → core (permanent), with auto-promotion
- **Hybrid search** — RRF (Reciprocal Rank Fusion) combining pgvector cosine similarity + PostgreSQL full-text search
- **A2A protocol** — 7 skills (memory write/search/read/share + session search/read/share), discoverable via Agent Card
- **Namespace isolation** — each agent gets private storage with optional cross-agent sharing
- **Client ingest API** — REST endpoints for [Mercury](https://github.com/fucheng830/mercury) clients to push session data
- **Knowledge graph** — automatic entity/relation extraction and graph traversal
- **LLM-powered iteration** — daily recap compression, weekly review, monthly knowledge consolidation via DeepSeek V4

## Architecture

```
                    ┌──────────────────────────────┐
                    │     External Agents           │
                    │  Claude / Gemini / Custom     │
                    └──────────┬───────────────────┘
                               │ A2A Protocol (v1.0)
                               ▼
┌──────────────────────────────────────────────────────────┐
│                    Mercury Server                          │
│  ┌────────────┐ ┌─────────────┐ ┌────────────────────┐  │
│  │ REST API   │ │ A2A Layer   │ │ MCP Server         │  │
│  │ /api/*     │ │ /.well-known│ │ hermes-memory MCP  │  │
│  └─────┬──────┘ └──────┬──────┘ └────────┬───────────┘  │
│        └───────────────┼─────────────────┘              │
│                        ▼                                │
│  ┌──────────────────────────────────────────────────┐   │
│  │          Namespace-Aware Memory Service           │   │
│  └──────────────────────────────────────────────────┘   │
│                        ▼                                │
│           PostgreSQL 17 + pgvector 0.8                   │
└──────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Clone
git clone https://github.com/fucheng830/mercury-server.git
cd mercury-server

# Install
pip install -r requirements.txt

# Configure — edit config.yaml with your DB and LLM settings
cp config.yaml config.local.yaml

# Run
python server.py --port 8788 --shared
```

## A2A Agent Card

```bash
curl http://localhost:8788/.well-known/agent-card.json
```

Returns 7 skills discoverable by any A2A-compatible agent.

## A2A Skills

| Skill | Description |
|-------|-------------|
| `memory.write` | Store a memory with layer, importance, and tags |
| `memory.search` | Hybrid search (vector + FTS) across namespaces |
| `memory.read` | Paginated memory read |
| `memory.share` | Move a memory to shared namespace |
| `session.search` | Search Claude Code sessions by content |
| `session.read` | Read session metadata |
| `session.share` | Share a session to all agents |

## Database

Requires PostgreSQL 17 with pgvector extension. See `hermes/schema.sql` for the full schema.

## Related

- [Mercury](https://github.com/fucheng830/mercury) — the client daemon
- [A2A Protocol](https://a2a-protocol.org) — the open agent-to-agent standard
- [pgvector](https://github.com/pgvector/pgvector) — vector similarity search for PostgreSQL
