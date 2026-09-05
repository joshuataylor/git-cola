"""Tests for the "Add to exclusions" dialog in cola.widgets.gitignore"""
import sys
from unittest.mock import MagicMock

import pytest

from cola.settings import Settings
from cola.widgets import gitignore
from cola.widgets import standard
from qtpy import QtWidgets


@pytest.fixture(scope='module')
def qapp():
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(sys.argv[:1] if sys.argv else ['test'])
    yield instance


@pytest.fixture
def view(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Settings, 'config_path', str(tmp_path / 'settings'))
    context = MagicMock()
    context.selection.untracked = ['foo', 'bar']
    dialog = gitignore.AddToGitIgnore(context, parent=None)
    yield dialog
    dialog.dispose()


def test_filename_is_prefilled_from_selection(view):
    assert view.edit_filename.text() == '/foo;/bar'
    assert not view.edit_filename.isEnabled()
    assert view.radio_filename.isChecked()
    assert view.radio_in_repo.isChecked()


def test_custom_pattern_enables_the_edit(view, qapp):
    view.radio_pattern.setChecked(True)
    # connect_toggle() uses a queued connection.
    qapp.processEvents()
    assert view.edit_filename.isEnabled()


def test_geometry_is_not_persisted(view):
    assert view.export_state() == {}
    assert view.restore_state() is False


def test_button_row_uses_the_shared_helper(view):
    assert view.button_apply.isDefault()
    if isinstance(view.button_box, QtWidgets.QDialogButtonBox):
        roles = QtWidgets.QDialogButtonBox
        assert view.button_box.buttonRole(view.button_apply) == roles.AcceptRole
        assert view.button_box.buttonRole(view.button_close) == roles.RejectRole
    else:
        assert isinstance(view.button_box, QtWidgets.QHBoxLayout)


def test_default_button_is_focused_on_macos(view, monkeypatch):
    """On macOS a sheet with no focus widget gives the default button focus"""
    monkeypatch.setattr(standard.utils, 'is_darwin', lambda: True)
    focused = []
    monkeypatch.setattr(
        view.button_apply, 'setFocus', lambda *args: focused.append(True)
    )
    # The dialog reaches its sheet with nothing focused; the default (Add)
    # button is the one that should receive focus so Return activates it.
    assert view.button_apply.isDefault()
    view._focus_default_button()
    assert focused == [True]


def test_default_button_focus_is_a_noop_off_macos(view, monkeypatch):
    monkeypatch.setattr(standard.utils, 'is_darwin', lambda: False)
    focused = []
    monkeypatch.setattr(
        view.button_apply, 'setFocus', lambda *args: focused.append(True)
    )
    view._focus_default_button()
    assert focused == []


def test_return_in_pattern_field_submits(view, qapp, monkeypatch):
    """Return in the focused pattern field triggers apply() (macOS sheet path)"""
    do = MagicMock()
    monkeypatch.setattr(gitignore.cmds, 'do', do)
    monkeypatch.setattr(view, 'accept', MagicMock())
    view.radio_pattern.setChecked(True)
    # connect_toggle() uses a queued connection.
    qapp.processEvents()
    view.edit_filename.setText('build/')
    view.edit_filename.returnPressed.emit()
    assert do.call_count == 1
    view.accept.assert_called_once()
