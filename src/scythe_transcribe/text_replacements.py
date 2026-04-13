"""Parse and apply user-defined keyword / phrase corrections to transcripts."""

from __future__ import annotations

import re
from typing import Final

# Accepts "->", "=>", Unicode arrows, or tab after the left side.
_SPLIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\s*(?:->|=>|→|⇒|\t)\s*",
)


def parse_replacement_spec(spec: str) -> list[tuple[str, str]]:
    """Parse multiline replacement rules into (from, to) pairs.

    One rule per line. Lines starting with ``#`` are comments. Empty lines are
    skipped. The separator between source and replacement can be ``->``,
    ``=>``, ``→``, ``⇒``, or a tab.

    Args:
        spec: Raw text from the user dictionary field.

    Returns:
        Ordered list of (from_text, to_text) pairs; empty ``from`` entries are
        dropped.
    """
    pairs: list[tuple[str, str]] = []
    for raw_line in spec.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = _SPLIT_PATTERN.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        src, dst = parts[0].strip(), parts[1].strip()
        if not src:
            continue
        pairs.append((src, dst))
    return pairs


def groq_asr_prompt_from_replacement_spec(
    spec: str, *, max_chars: int = 1000
) -> str | None:
    """Build a Groq Whisper ``prompt`` string from keyword dictionary rules.

    Lists unique ``from`` and ``to`` phrases so the ASR model can bias toward
    the user's terms before post-hoc replacement runs.

    Args:
        spec: Raw multiline keyword dictionary (same format as
            :func:`parse_replacement_spec`).
        max_chars: Maximum length of the returned string (truncated at a comma).

    Returns:
        A non-empty instruction string, or ``None`` if there are no usable
        rules.
    """
    pairs = parse_replacement_spec(spec)
    if not pairs:
        return None
    seen: set[str] = set()
    ordered: list[str] = []
    for src, dst in pairs:
        for part in (src.strip(), dst.strip()):
            if not part:
                continue
            key = part.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(part)
    if not ordered:
        return None
    body = ", ".join(ordered)
    prefix = "When transcribing, use these terms and spellings where appropriate: "
    text = prefix + body
    if len(text) <= max_chars:
        return text
    budget = max_chars - len(prefix) - 1
    if budget < 8:
        return prefix.strip()
    truncated = body[:budget]
    cut = truncated.rfind(", ")
    if cut > 0:
        truncated = truncated[:cut]
    return prefix + truncated + "…"


def apply_replacements(text: str, pairs: list[tuple[str, str]]) -> str:
    """Apply replacements longest-first to reduce accidental partial matches.

    Args:
        text: Input transcript or other string.
        pairs: Replacement rules in user order; applied in descending length
            of the ``from`` string (then original order as tiebreaker).

    Returns:
        Updated string.
    """
    if not text or not pairs:
        return text
    ordered = sorted(enumerate(pairs), key=lambda it: (-len(it[1][0]), it[0]))
    out = text
    for _, (src, dst) in ordered:
        if not src:
            continue
        out = out.replace(src, dst)
    return out
