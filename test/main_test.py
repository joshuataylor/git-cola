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

    # Closing the window saves settings (recent repos, window geometry). Point
    # the settings file at a throwaway path so the test never writes to the
    # developer's real ~/.config/git-cola/settings.
    monkeypatch.setattr(Settings, 'config_path', str(tmp_path / 'settings'))

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


def test_bookmarks_docks_start_without_widgets(main_view):
    """The Favorites/Recent widgets are not constructed at startup

    Both docks are hidden by default, so their widgets are built lazily on
    the dock's first reveal instead of on the startup critical path.
    """
    assert main_view.bookmarkswidget is None
    assert main_view.recentwidget is None
    assert main_view.bookmarksdock.widget() is None
    assert main_view.recentdock.widget() is None
    # The copy overrides skip the missing trees but keep the others.
    copy_widgets = main_view.edit_proxy.overrides['copy']
    assert main_view.statuswidget.tree in copy_widgets


def test_bookmarks_dock_builds_on_first_reveal(main_view):
    """Revealing the Favorites dock builds and wires its widget once"""
    main_view.bookmarksdock.visibilityChanged.emit(True)

    widget = main_view.bookmarkswidget
    assert widget is not None
    assert main_view.bookmarksdock.widget() is widget
    assert widget.tree in main_view.edit_proxy.overrides['copy']
    assert widget.font() == main_view.font()

    # A second reveal must not rebuild the widget.
    main_view.bookmarksdock.visibilityChanged.emit(True)
    assert main_view.bookmarksdock.widget() is widget


def test_both_bookmarks_docks_build_when_revealed(main_view):
    """Revealing both docks builds both widgets and connects them"""
    main_view.bookmarksdock.visibilityChanged.emit(True)
    main_view.recentdock.visibilityChanged.emit(True)

    assert main_view.bookmarkswidget is not None
    assert main_view.recentwidget is not None
    copy_widgets = main_view.edit_proxy.overrides['copy']
    assert main_view.bookmarkswidget.tree in copy_widgets
    assert main_view.recentwidget.tree in copy_widgets
