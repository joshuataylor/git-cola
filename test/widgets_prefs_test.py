"""Tests for the Preferences window (cola.widgets.prefs)."""
import sys
from unittest.mock import MagicMock

import pytest

from cola import core
from cola import git
from cola import gitcfg
from cola import gitcmds
from cola import operations_local
from cola.models import main as main_model
from cola.settings import Settings
from cola.widgets import prefs as prefs_widget
from qtpy import QtCore
from qtpy import QtWidgets

from . import helper


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for the widget tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


@pytest.fixture
def context(qapp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    helper.initialize_repo()
    monkeypatch.setattr(Settings, 'config_path', str(tmp_path / 'settings'))
    monkeypatch.setattr(prefs_widget, '_open_view', None, raising=False)
    ctx = MagicMock()
    ctx.ops = operations_local.LocalOperations()
    ctx.git = git.create(ctx.ops)
    ctx.git.set_worktree(core.getcwd())
    ctx.cfg = gitcfg.create(ctx)
    ctx.model = main_model.create(ctx)
    ctx.settings = Settings()
    ctx.cfg.reset()
    gitcmds.reset()
    return ctx


def test_preferences_leave_the_main_window_usable(context):
    """Settings apply as they change, so the window behind stays usable"""
    parent = QtWidgets.QWidget()
    view = prefs_widget.preferences(context, parent=parent)
    try:
        assert view.windowModality() == QtCore.Qt.NonModal
        assert not view.isModal()
    finally:
        view.close()


def test_preferences_open_once(context):
    """Opening Preferences again raises the open window"""
    parent = QtWidgets.QWidget()
    first = prefs_widget.preferences(context, parent=parent)
    try:
        assert prefs_widget.preferences(context, parent=parent) is first
        first.close()
        second = prefs_widget.preferences(context, parent=parent)
        assert second is not first
        second.close()
    finally:
        first.close()
