"""Tests for pure helpers in hermes.extractor (no DB)."""
from hermes.extractor import _project_name, _sessions_text_for_project


def test_project_name_posix():
    assert _project_name("/home/x/mercury-server") == "mercury-server"


def test_project_name_windows_backslashes():
    assert _project_name("D:\\workspace\\projects\\ragi") == "ragi"


def test_project_name_trailing_slash():
    assert _project_name("/a/b/c/") == "c"


def test_project_name_empty_falls_back():
    assert _project_name("") == "unknown"


def test_sessions_text_includes_user_prompts_and_tools():
    class S:
        project = "/p"
        user_prompts = ["how to do X"]
        assistant_summaries = []
        tools_used = {"Bash": 2, "Edit": 1}
    text = _sessions_text_for_project([S()])
    assert "how to do X" in text
    assert "Bash(2)" in text and "Edit(1)" in text
