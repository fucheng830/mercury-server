"""Tests for recap.llm_service._parse_json — fence stripping + brace fallback.

Guards the bug where the 'auto' model wraps JSON in ```json fences, which silently
broke extraction (extracted=0) before this helper existed.
"""
import pytest

from recap.llm_service import _parse_json


def test_plain_json():
    assert _parse_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_json_code_fence():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_bare_code_fence():
    assert _parse_json('```\n{"a": 1}\n```') == {"a": 1}


def test_fence_internal_whitespace():
    assert _parse_json('```json\n{"a": 1}\n   \n```')["a"] == 1


def test_brace_fallback_strips_surrounding_text():
    assert _parse_json('here is the data {"a": 1} done') == {"a": 1}


def test_brace_fallback_nested():
    assert _parse_json('noise {"a": {"b": 2}, "c": 3} tail') == {"a": {"b": 2}, "c": 3}


def test_invalid_raises():
    with pytest.raises(Exception):
        _parse_json("not json at all")


def test_empty_raises():
    with pytest.raises(Exception):
        _parse_json("")
