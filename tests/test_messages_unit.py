"""Unit tests for extract_text_turns (pure logic, no DB, no embedding service).

Pins the ingest contract: which JSONL message shapes become rows, which are
dropped, and how seq is assigned. Run with `python -m pytest tests/test_messages_unit.py -q`.
"""
from hermes.messages_service import extract_text_turns


def _user(text, ts="2026-07-05T10:00:00Z"):
    return {"type": "user", "message": {"content": text}, "timestamp": ts}


def _user_blocks(blocks, ts="2026-07-05T10:00:00Z"):
    return {"type": "user", "message": {"content": blocks}, "timestamp": ts}


def _assistant(text, ts="2026-07-05T10:00:01Z"):
    return {"type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
            "timestamp": ts}


def _assistant_text_plus_tool(text, name="Bash", inp=None,
                              ts="2026-07-05T10:00:01Z"):
    return {"type": "assistant",
            "message": {"content": [
                {"type": "text", "text": text},
                {"type": "tool_use", "id": "t1", "name": name, "input": inp or {}},
            ]}, "timestamp": ts}


def _tool_result(ts="2026-07-05T10:00:02Z", tool_use_id="t1"):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": "output..."},
    ]}, "timestamp": ts}


def test_user_string_content_becomes_one_turn():
    turns = extract_text_turns([_user("hello world")])
    assert len(turns) == 1
    assert turns[0] == {"seq": 0, "role": "user",
                        "content_text": "hello world",
                        "created_ts": "2026-07-05T10:00:00Z"}


def test_assistant_text_kept_tool_use_dropped():
    msgs = [
        _user("run the tests"),
        _assistant_text_plus_tool("running pytest", "Bash", {"command": "pytest -q"}),
        _tool_result(),
        _assistant("all green"),
    ]
    turns = extract_text_turns(msgs)
    assert [t["role"] for t in turns] == ["user", "assistant", "assistant"]
    assert turns[1]["content_text"] == "running pytest"
    assert turns[2]["content_text"] == "all green"


def test_tool_result_only_user_wrapper_dropped():
    assert extract_text_turns([_tool_result()]) == []


def test_thinking_blocks_and_empty_text_skipped():
    msgs = [
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "internal reasoning"},
        ]}, "timestamp": ""},
        _assistant(""),  # empty text → dropped
    ]
    assert extract_text_turns(msgs) == []


def test_seq_increments_only_on_kept_turns():
    msgs = [
        _tool_result(),                      # dropped
        _user("first"),                      # seq 0
        _assistant_text_plus_tool("a", "Bash"),  # seq 1
        _tool_result(),                      # dropped
        _assistant("second"),                # seq 2
    ]
    assert [t["seq"] for t in extract_text_turns(msgs)] == [0, 1, 2]


def test_user_mixed_text_and_tool_result_dropped():
    """Pin claude.py-consistent semantics: any tool_result presence on a user
    message drops the whole message (text + tool_result together)."""
    turns = extract_text_turns([_user_blocks([
        {"type": "text", "text": "what does this output mean?"},
        {"type": "tool_result", "tool_use_id": "t1", "content": "output"},
    ])])
    assert turns == []


def test_non_conversation_types_ignored():
    msgs = [
        {"type": "system", "message": {"content": "system msg"}, "timestamp": ""},
        {"type": "file-history-snapshot", "snapshot": {}},
        _user("real user"),
    ]
    turns = extract_text_turns(msgs)
    assert len(turns) == 1 and turns[0]["content_text"] == "real user"
