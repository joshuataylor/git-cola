"""Tests for the DAG file list (cola.widgets.filelist)."""
import sys
from unittest.mock import MagicMock

import pytest

from cola.models import dag
from cola.widgets.filelist import FileWidget
from cola.widgets.filelist import gather_files
from qtpy import QtGui
from qtpy import QtWidgets
from qtpy.QtCore import Qt


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
def file_widget(qapp):
    widget = FileWidget(MagicMock(), None)
    widget.resize(400, 300)
    widget.show()
    qapp.processEvents()
    try:
        yield widget, qapp
    finally:
        widget.close()


def test_filename_column_grows_with_the_widget(file_widget):
    """Until the user intervenes, the Filename column absorbs free space."""
    widget, qapp = file_widget
    initial = widget.columnWidth(0)

    widget.resize(800, 300)
    qapp.processEvents()

    assert widget.columnWidth(0) > initial


def test_manual_column_width_is_kept_across_resizes(file_widget):
    """A user-dragged column width is not snapped back on the next resize."""
    widget, qapp = file_widget

    # Simulate the user dragging the Filename column border.
    widget.header().resizeSection(0, 350)
    qapp.processEvents()
    assert widget._user_adjusted_columns is True

    widget.resize(1000, 300)
    qapp.processEvents()

    assert widget.columnWidth(0) == 350


def test_programmatic_resize_is_not_mistaken_for_a_drag(file_widget):
    """_resize_columns() must not flip the user-adjusted flag on itself."""
    widget, _qapp = file_widget

    widget._resize_columns()

    assert widget._user_adjusted_columns is False


def test_file_list_scrolls_per_pixel(file_widget):
    """The tree inherits smooth per-pixel scrolling from standard.TreeWidget."""
    widget, _qapp = file_widget
    per_pixel = QtWidgets.QAbstractItemView.ScrollPerPixel
    assert widget.verticalScrollMode() == per_pixel


def _commit(oid):
    commit = MagicMock()
    commit.oid = oid
    return commit


def test_selection_is_debounced(file_widget):
    """Rapid selections coalesce into a single background load."""
    widget, _qapp = file_widget

    widget.commits_selected([_commit('a' * 40)])
    widget.commits_selected([_commit('b' * 40)])
    widget.commits_selected([_commit('c' * 40)])

    # No git work happens on the selection path; the load is pending.
    assert widget.context.runtask.start.call_count == 0
    assert widget._pending_selection == ('oid', 'c' * 40)
    assert widget._files_timer.isActive()

    widget._load_pending_files()

    assert widget.context.runtask.start.call_count == 1
    assert widget._pending_selection is None


def test_stale_results_are_dropped(file_widget):
    """A result from a superseded load must not repopulate the list."""
    widget, _qapp = file_widget
    widget.list_files = MagicMock()

    stale_token = widget._files_token
    widget._files_token += 1
    widget._files_ready(stale_token, ('oid', 'a' * 40), ['1\t2\ta.txt'])

    widget.list_files.assert_not_called()


def test_current_results_are_applied_and_cached(file_widget):
    """A current result renders and is remembered for revisits."""
    widget, _qapp = file_widget
    widget.list_files = MagicMock()
    key = ('oid', 'a' * 40)
    lines = ['1\t2\ta.txt']

    widget._files_ready(widget._files_token, key, lines)

    widget.list_files.assert_called_once_with(lines)
    assert widget._files_cache[key] == lines


def test_cache_hit_skips_the_background_task(file_widget):
    """Revisiting a cached commit renders without starting a task."""
    widget, _qapp = file_widget
    widget.list_files = MagicMock()
    key = ('oid', 'a' * 40)
    lines = ['1\t2\ta.txt']
    widget._files_ready(widget._files_token, key, lines)
    widget.list_files.reset_mock()

    widget.commits_selected([_commit('a' * 40)])
    widget._load_pending_files()

    widget.list_files.assert_called_once_with(lines)
    assert widget.context.runtask.start.call_count == 0


def test_volatile_selections_are_not_cached(file_widget):
    """WORKTREE and STAGE file lists are recomputed every time."""
    widget, _qapp = file_widget
    widget.list_files = MagicMock()

    widget._files_ready(widget._files_token, ('oid', dag.WORKTREE), ['1\t2\ta.txt'])
    widget._files_ready(widget._files_token, ('range', dag.STAGE, 'a' * 40), ['x'])

    assert not widget._files_cache


def test_empty_selection_cancels_the_pending_load(file_widget):
    """Clearing the selection drops pending and in-flight loads."""
    widget, _qapp = file_widget
    widget.list_files = MagicMock()

    widget.commits_selected([_commit('a' * 40)])
    token = widget._files_token
    widget.commits_selected([])

    assert widget._pending_selection is None
    assert not widget._files_timer.isActive()
    # The token was bumped, so an in-flight result is now stale.
    widget._files_ready(token, ('oid', 'a' * 40), ['1\t2\ta.txt'])
    widget.list_files.assert_not_called()


def _press_key(widget, key, modifier=Qt.NoModifier):
    """Deliver a KeyPress event to the widget and return whether it was handled"""
    event = QtGui.QKeyEvent(QtGui.QKeyEvent.KeyPress, key, modifier)
    QtWidgets.QApplication.sendEvent(widget, event)
    return event.isAccepted()


@pytest.mark.parametrize('key', [Qt.Key_Return, Qt.Key_Enter])
def test_shift_enter_scopes_dag_to_selection(file_widget, key):
    """Shift+Enter on a selected file emits histories_selected."""
    widget, _qapp = file_widget
    scoped = MagicMock()
    widget.histories_selected.connect(scoped)
    widget.list_files(['1\t2\ta.txt'])
    widget.topLevelItem(0).setSelected(True)

    assert _press_key(widget, key, Qt.ShiftModifier) is True
    scoped.assert_called_once_with(['a.txt'])


def test_shift_enter_scopes_dag_to_multiple_files(file_widget):
    """Show History supports multiple selected files."""
    widget, _qapp = file_widget
    scoped = MagicMock()
    widget.histories_selected.connect(scoped)
    widget.list_files(['1\t2\ta.txt', '3\t4\tb.txt'])
    widget.topLevelItem(0).setSelected(True)
    widget.topLevelItem(1).setSelected(True)

    _press_key(widget, Qt.Key_Return, Qt.ShiftModifier)
    scoped.assert_called_once_with(['a.txt', 'b.txt'])


def test_plain_enter_is_not_intercepted(file_widget):
    """Enter without Shift falls through to the base class."""
    widget, _qapp = file_widget
    widget.show_history = MagicMock()
    widget.list_files(['1\t2\ta.txt'])
    widget.topLevelItem(0).setSelected(True)

    _press_key(widget, Qt.Key_Return)
    widget.show_history.assert_not_called()


def test_non_enter_key_does_not_scope_the_dag(file_widget):
    """A plain key is not intercepted by the Shift+Enter handler."""
    widget, _qapp = file_widget
    widget.show_history = MagicMock()
    widget.list_files(['1\t2\ta.txt'])
    widget.topLevelItem(0).setSelected(True)

    _press_key(widget, Qt.Key_A, Qt.ShiftModifier)
    widget.show_history.assert_not_called()


def test_gather_files_single_commit():
    """A single commit uses "git show --numstat" and NUL splitting."""
    context = MagicMock()
    context.git.show.return_value = (0, '1\t2\ta.txt\x001\t2\tb.txt\x00', '')

    paths = gather_files(context, ('oid', 'a' * 40))

    context.git.show.assert_called_once_with(
        'a' * 40,
        format='',
        numstat=True,
        no_renames=True,
        z=True,
        _readonly=True,
    )
    assert paths == ['1\t2\ta.txt', '1\t2\tb.txt']


def test_gather_files_stage():
    """The STAGE pseudo-commit diffs the index and splits on newlines."""
    context = MagicMock()
    context.git.diff_index.return_value = (0, '1\t2\ta.txt\n1\t2\tb.txt\n', '')

    paths = gather_files(context, ('oid', dag.STAGE))

    context.git.diff_index.assert_called_once_with(
        'HEAD', cached=True, numstat=True, _readonly=True
    )
    assert paths == ['1\t2\ta.txt', '1\t2\tb.txt']


def test_gather_files_worktree():
    """The WORKTREE pseudo-commit uses "git diff-files"."""
    context = MagicMock()
    context.git.diff_files.return_value = (0, '1\t2\ta.txt\n', '')

    paths = gather_files(context, ('oid', dag.WORKTREE))

    context.git.diff_files.assert_called_once_with(numstat=True, _readonly=True)
    assert paths == ['1\t2\ta.txt']


def test_gather_files_range():
    """A commit range diffs from the start commit's parent to the end."""
    context = MagicMock()
    context.git.diff.return_value = (0, '1\t2\ta.txt\x00', '')

    paths = gather_files(context, ('range', 'a' * 40, 'b' * 40))

    context.git.diff.assert_called_once_with(
        'a' * 40 + '~', 'b' * 40, z=True, numstat=True, no_renames=True
    )
    assert paths == ['1\t2\ta.txt']


def test_gather_files_range_stage_to_worktree():
    """A STAGE..WORKTREE range diffs the worktree with no arguments."""
    context = MagicMock()
    context.git.diff.return_value = (0, '1\t2\ta.txt\x00', '')

    paths = gather_files(context, ('range', dag.STAGE, dag.WORKTREE))

    context.git.diff.assert_called_once_with(z=True, numstat=True, no_renames=True)
    assert paths == ['1\t2\ta.txt']


def test_gather_files_failure_returns_no_paths():
    """A failing git call clears the list rather than raising."""
    context = MagicMock()
    context.git.show.return_value = (1, '', 'fatal: bad object')

    assert gather_files(context, ('oid', 'a' * 40)) == []
