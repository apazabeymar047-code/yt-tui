"""Tests for the mouse-report filter used by the search box."""
from yt_tui.ui import SafeInput


def test_detects_mouse_junk():
    # Restos tipicos que deja Textual en Windows
    assert SafeInput._looks_like_junk("[<0;126;")
    assert SafeInput._looks_like_junk("<32;124;5M")
    assert SafeInput._looks_like_junk("<0;")
    assert SafeInput._looks_like_junk("[<32;90;4M")


def test_does_not_flag_normal_text():
    assert not SafeInput._looks_like_junk("<")
    assert not SafeInput._looks_like_junk("[")
    assert not SafeInput._looks_like_junk("a")
    assert not SafeInput._looks_like_junk("bad bunny")
    assert not SafeInput._looks_like_junk("2024")
    assert not SafeInput._looks_like_junk("hola<3")


def test_tail_alphabet_excludes_new_sequence_start():
    # Los restos no pueden empezar una secuencia nueva: eso lo hace el buffer
    assert "[" not in SafeInput._TAIL_ALPHABET
    assert "<" not in SafeInput._TAIL_ALPHABET
    assert "M" in SafeInput._TAIL_ALPHABET
