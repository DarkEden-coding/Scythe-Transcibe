"""Tests for keyword replacement parsing and application."""

from __future__ import annotations

from scythe_transcribe.text_replacements import (
    apply_replacements,
    groq_asr_prompt_from_replacement_spec,
    parse_replacement_spec,
)
from scythe_transcribe.transcribe_pipeline import _chunk_transcript_for_postprocess


def test_parse_arrow_variants() -> None:
    """Rules accept common arrow separators."""
    spec = "a -> b\nx => y\np → q"
    assert parse_replacement_spec(spec) == [("a", "b"), ("x", "y"), ("p", "q")]


def test_parse_skips_comments_and_blank() -> None:
    """Comments and empty lines are ignored."""
    spec = "\n# ignore\nfoo -> bar\n\n"
    assert parse_replacement_spec(spec) == [("foo", "bar")]


def test_parse_drops_empty_source() -> None:
    """Lines without a valid left-hand side are skipped."""
    assert parse_replacement_spec(" -> x") == []


def test_apply_longest_first() -> None:
    """Longer matches are applied first to avoid partial clobbering."""
    pairs = [("ab", "X"), ("abc", "Y")]
    assert apply_replacements("say abc end", pairs) == "say Y end"


def test_apply_empty_inputs() -> None:
    """Empty text or rules is a no-op."""
    assert apply_replacements("", [("a", "b")]) == ""
    assert apply_replacements("hello", []) == "hello"


def test_groq_asr_prompt_from_spec() -> None:
    """ASR prompt lists dictionary terms in first-seen order."""
    spec = "foo -> bar\n# c\nbaz -> qux"
    p = groq_asr_prompt_from_replacement_spec(spec)
    assert p is not None
    assert "foo" in p and "bar" in p and "baz" in p and "qux" in p
    assert p.startswith("When transcribing")


def test_groq_asr_prompt_empty_spec() -> None:
    """No rules yields no ASR prompt."""
    assert groq_asr_prompt_from_replacement_spec("") is None
    assert groq_asr_prompt_from_replacement_spec("# only\n") is None


def test_chunk_transcript_short_unchanged() -> None:
    """Text under the max stays a single chunk."""
    t = "hello\n\nworld"
    assert _chunk_transcript_for_postprocess(t, 10_000) == [t]


def test_chunk_transcript_hard_splits_long_run() -> None:
    """Very long segments without paragraph breaks are sliced."""
    t = "x" * 5000
    parts = _chunk_transcript_for_postprocess(t, 2000)
    assert len(parts) == 3
    assert "".join(parts) == t
