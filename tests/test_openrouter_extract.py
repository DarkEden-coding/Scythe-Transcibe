"""Tests for OpenRouter chat response parsing."""

from __future__ import annotations

from scythe_transcribe.openrouter_client import _extract_assistant_text


def test_extract_plain_string_content() -> None:
    """String content is returned."""
    data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "  hello  ",
                }
            }
        ]
    }
    assert _extract_assistant_text(data) == "hello"


def test_extract_list_content_blocks() -> None:
    """List-of-blocks content is joined."""
    data = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "a"},
                        {"type": "text", "text": "b"},
                    ]
                }
            }
        ]
    }
    assert _extract_assistant_text(data) == "a\nb"


def test_extract_missing_choices() -> None:
    """Malformed payloads return empty string."""
    assert _extract_assistant_text({}) == ""
