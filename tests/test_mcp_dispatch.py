"""Tests for the stateless MCP JSON-RPC dispatcher (protocol correctness, no DB).

Covers initialize / notifications / ping / tools-list / tools-call error paths.
Tool execution itself is DB-backed and exercised by the integration suite.
"""
from hermes import mcp_server


def test_initialize():
    r = mcp_server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18"}})
    assert r["id"] == 1
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in r["result"]["capabilities"]
    assert r["result"]["serverInfo"]["name"] == "mercury-memory"


def test_initialize_echoes_client_protocol_version():
    r = mcp_server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2024-11-05"}})
    assert r["result"]["protocolVersion"] == "2024-11-05"


def test_initialized_notification_returns_none():
    assert mcp_server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ping():
    assert mcp_server.dispatch({"jsonrpc": "2.0", "id": 5, "method": "ping"}) == \
        {"jsonrpc": "2.0", "id": 5, "result": {}}


def test_tools_list_includes_new_tools():
    r = mcp_server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in r["result"]["tools"]}
    assert {"recall_memory", "search_memory", "associate_memory", "memory_stats"} <= names


def test_tools_list_schema_well_formed():
    r = mcp_server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    for t in r["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"
        assert "description" in t


def test_tools_call_unknown_tool_errors():
    r = mcp_server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "nope", "arguments": {}}})
    assert r["error"]["code"] == -32601
    assert r["id"] == 3


def test_unknown_method_request_errors():
    r = mcp_server.dispatch({"jsonrpc": "2.0", "id": 4, "method": "foo/bar"})
    assert r["error"]["code"] == -32601


def test_unknown_method_notification_is_silent():
    assert mcp_server.dispatch({"jsonrpc": "2.0", "method": "foo/bar"}) is None


def test_batch_with_notification_and_request():
    batch = [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 9, "method": "ping"},
    ]
    responses = [mcp_server.dispatch(m) for m in batch]
    responses = [r for r in responses if r is not None]
    assert responses == [{"jsonrpc": "2.0", "id": 9, "result": {}}]
