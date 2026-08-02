"""Tests for the diff editor empty-state hint (cola.widgets.diff)."""
import sys
from unittest.mock import MagicMock

import pytest

from cola import core
from cola import git
from cola import gitcfg
from cola import gitcmds
from cola.models import main as main_model
from cola.widgets.diff import DiffEditor
from cola.widgets.diff import Options
from cola.widgets.text import PlainTextLabel
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
def diff_editor(qapp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    helper.initialize_repo()

    context = MagicMock()
    context.git = git.create()
    context.git.set_worktree(core.getcwd())
    context.cfg = gitcfg.create(context)
    context.model = main_model.create(context)
    context.cfg.reset()
    gitcmds.reset()

    parent = QtWidgets.QWidget()
    options = Options(parent, filename=PlainTextLabel(parent=parent))
    editor = DiffEditor(context, options, parent)
    try:
        yield editor
    finally:
        editor.close()


def test_diff_editor_shows_empty_state_hint(diff_editor):
    """With no diff loaded the pane guides the user instead of showing blank."""
    assert diff_editor.placeholderText() == 'Select a file to view its diff'


def test_options_spacer_pins_gear_left_when_filename_hidden(qapp):
    """Hiding the filename shows the spacer so the gear button stays left."""
    parent = QtWidgets.QWidget()
    options = Options(parent, filename=PlainTextLabel(parent=parent))
    try:
        # With the filename shown the greedy label claims the slack, so the
        # spacer stays hidden.
        assert options.spacer.isHidden()

        options.set_filename_visible(False)
        assert not options.spacer.isHidden()

        options.set_filename_visible(True)
        assert options.spacer.isHidden()
    finally:
        options.close()
        parent.close()
