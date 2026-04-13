"""Tests for global hotkey token normalization."""

from __future__ import annotations

from pynput.keyboard import Key, KeyCode

from scythe_transcribe.hotkey_service import _key_event_token, _parse_hotkey_combo


def test_parse_hotkey_combo_aliases_meta_and_control() -> None:
    """Stored hotkey aliases normalize to the frontend token vocabulary."""
    assert _parse_hotkey_combo("Control + Option + Space") == ["ctrl", "alt", "space"]
    assert _parse_hotkey_combo("Cmd+Command+Win+Super+OS") == [
        "meta",
        "meta",
        "meta",
        "meta",
        "meta",
    ]


def test_key_event_token_maps_option_space_variants_to_space() -> None:
    """macOS Option+Space can arrive as NBSP or the platform space vk."""
    assert _key_event_token(Key.space) == "space"
    assert _key_event_token(KeyCode(char="\xa0")) == "space"

    space_vk = getattr(Key.space.value, "vk", None)
    assert space_vk is not None
    assert _key_event_token(KeyCode(char="\xa0", vk=space_vk)) == "space"


def test_key_event_token_maps_function_key_keycode_by_vk() -> None:
    """macOS can send function keys as private-use chars with f-key vks."""
    f5_vk = getattr(Key.f5.value, "vk", None)
    assert f5_vk is not None
    assert _key_event_token(KeyCode(char="\ue001", vk=f5_vk)) == "f5"
