#!/usr/bin/env bash
# Claude Code SessionStart hook — inject this project's active memories.
# Output (stdout) is added to the session's startup context by Claude Code.
# Configure in ~/.claude/settings.json:
#   "hooks": { "SessionStart": [{ "hooks": [{ "type": "command",
#       "command": "bash /path/to/recall_hook.sh" }] }] }
set -euo pipefail

PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
API="${MERCURY_API:-http://127.0.0.1:8788}"

# urlencode the project path (fall back to raw if python missing)
ENCODED=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$PROJECT" 2>/dev/null || printf '%s' "$PROJECT")

RESP=$(curl -s --max-time 5 "${API}/api/memory/recall?project=${ENCODED}&limit=30" 2>/dev/null || true)
[ -z "$RESP" ] && exit 0

COUNT=$(printf '%s' "$RESP" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('count',0))" 2>/dev/null || echo 0)
[ "$COUNT" = "0" ] && exit 0

echo "## 项目记忆（agent 自动注入 — 本项目已沉淀的约定/决策/坑，按需遵守）"
printf '%s' "$RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for it in d.get('items', []):
    t = it.get('type', 'NOTE')
    imp = it.get('importance', 3)
    c = (it.get('content', '') or '').replace('\n', ' ')[:200]
    print(f'- [{t}★{imp}] {c}')
"
