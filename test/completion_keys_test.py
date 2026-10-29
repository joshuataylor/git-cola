"""Tests for the completion popup key"""
from cola.widgets import completion
from qtpy.QtCore import Qt


def test_ctrl_space_shows_the_popup_everywhere(monkeypatch):
    for darwin in (False, True):
        monkeypatch.setattr(completion.core, 'IS_DARWIN', darwin)
        assert completion.is_popup_key(Qt.Key_Space, Qt.ControlModifier)


def test_macos_also_accepts_option_esc_and_control_space(monkeypatch):
    """Cmd+Space is Spotlight, so it never reaches the app"""
    monkeypatch.setattr(completion.core, 'IS_DARWIN', True)
    assert completion.is_popup_key(Qt.Key_Escape, Qt.AltModifier)
    assert completion.is_popup_key(Qt.Key_Space, Qt.MetaModifier)
    assert completion.is_popup_key(Qt.Key_Escape, Qt.AltModifier | Qt.KeypadModifier)


def test_other_keys_do_not_show_the_popup(monkeypatch):
    monkeypatch.setattr(completion.core, 'IS_DARWIN', True)
    assert not completion.is_popup_key(Qt.Key_Escape, Qt.NoModifier)
    assert not completion.is_popup_key(Qt.Key_Space, Qt.NoModifier)
    assert not completion.is_popup_key(Qt.Key_Space, Qt.ShiftModifier)
    monkeypatch.setattr(completion.core, 'IS_DARWIN', False)
    assert not completion.is_popup_key(Qt.Key_Escape, Qt.AltModifier)
    assert not completion.is_popup_key(Qt.Key_Space, Qt.MetaModifier)


def test_completer_skips_model_updates_while_its_popup_is_hidden():
    """A status refresh must not rebuild completions nobody is looking at"""
    from unittest.mock import MagicMock

    model = MagicMock()
    completer = completion.Completer.__new__(completion.Completer)
    completer._model = model
    popup = MagicMock()
    completer.popup = lambda: popup

    popup.isVisible.return_value = False
    completer.update()
    model.update.assert_not_called()

    popup.isVisible.return_value = True
    completer.update()
    model.update.assert_called_once_with()
