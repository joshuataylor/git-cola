"""Tests for the "Add to exclusions" dialog in cola.widgets.gitignore"""
import sys
from unittest.mock import MagicMock

import pytest

from cola.settings import Settings
from cola.widgets import gitignore
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
