"""Tests for the main window (cola.widgets.main)."""
import sys
from unittest.mock import MagicMock

import pytest

from cola import core
from cola import git
from cola import gitcfg
from cola import gitcmds
from cola.models import main as main_model
from cola.settings import Settings
from cola.widgets import main as main_widget
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
def main_view(qapp, tmp_path, monkeypatch):
    """Construct a real MainView backed by a throwaway repository.

    The context is a MagicMock so the many collaborators the window touches
    (fsmonitor, runtask, notifier, ...) resolve to no-op stand-ins, but the
    pieces the window actually reads -- git, cfg, model and settings -- are
    real so construction exercises the production code path.
    """
    monkeypatch.chdir(tmp_path)
    helper.initialize_repo()

    context = MagicMock()
    context.git = git.create()
    context.git.set_worktree(core.getcwd())
    context.cfg = gitcfg.create(context)
    context.model = main_model.create(context)
    context.settings = Settings()
    context.cfg.reset()
    gitcmds.reset()

    view = main_widget.MainView(context)
    try:
        yield view
    finally:
        view.close()


def test_app_menu_actions_have_explicit_roles(main_view):
    """About/Preferences/Quit carry explicit macOS menu roles.

    Qt otherwise falls back to matching the English label text, which breaks
    once the labels are translated and leaves the app-menu placement wrong.
    """
    action = QtWidgets.QAction
    assert main_view.preferences_action.menuRole() == action.PreferencesRole
    assert main_view.quit_action.menuRole() == action.QuitRole
    assert main_view.help_about_action.menuRole() == action.AboutRole
