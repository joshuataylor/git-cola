"""Deterministic tests for the File History window"""
import sys
from unittest.mock import MagicMock

import pytest

from cola.models import filehistory
from cola.widgets import filehistory as filehistory_widget
from qtpy import QtWidgets

from .helper import app_context

# Prevent unused imports lint errors.
assert app_context is not None


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for widget tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


def _entry(oid, path='file.txt', old_path='', status='M'):
    return filehistory.FileHistoryEntry(
        oid,
        'summary',
        'Alice',
        'alice@example.com',
        '2026-01-01',
        status,
        path,
        old_path,
    )


def _make_window(app_context):
    app_context.settings = MagicMock()
    app_context.settings.get_gui_state.return_value = {}
    app_context.runtask = MagicMock()
    window = filehistory_widget.FileHistoryWindow(app_context, 'file.txt')
    # Observe diff loading without running real git.
    window.diffwidget = MagicMock()
    return window


def test_load_starts_background_task(qapp, app_context):
    window = _make_window(app_context)
    window.load()
    assert app_context.runtask.start.call_count == 1


def test_stale_load_results_are_dropped(qapp, app_context):
    window = _make_window(app_context)
    window.load()
    stale_token = window._load_token
    window.load()

    window.set_entries([_entry('a' * 40)], token=stale_token)
    assert window.treewidget.topLevelItemCount() == 0

    window.set_entries([_entry('a' * 40)], token=window._load_token)
    assert window.treewidget.topLevelItemCount() == 1


def test_selection_debounces_the_diff_load(qapp, app_context):
    window = _make_window(app_context)
    entries = [_entry('a' * 40), _entry('b' * 40)]
    window.set_entries(entries)

    # Selecting row 0 arms the debounce timer without loading a diff.
    assert window._pending_entry is entries[0]
    assert window._diff_timer.isActive()
    window.diffwidget.set_diff_oid.assert_not_called()

    # The timer firing loads the settled-on entry's file-scoped diff.
    window._diff_timer.stop()
    window._show_pending_entry()
    window.diffwidget.set_details.assert_called_once_with(
        'a' * 40, 'Alice', 'alice@example.com', '2026-01-01', 'summary'
    )
    window.diffwidget.set_diff_oid.assert_called_once_with(
        'a' * 40, filename='file.txt'
    )

    # Nothing pending: firing again is a no-op.
    window._show_pending_entry()
    assert window.diffwidget.set_diff_oid.call_count == 1


def test_rename_entries_diff_both_paths(qapp, app_context):
    window = _make_window(app_context)
    entry = _entry('a' * 40, path='new.txt', old_path='old.txt', status='R100')
    window.set_entries([entry])

    window._diff_timer.stop()
    window._show_pending_entry()
    window.diffwidget.set_diff_oid.assert_called_once_with(
        'a' * 40, filename=('new.txt', 'old.txt')
    )


def test_reload_preserves_the_selected_commit(qapp, app_context):
    window = _make_window(app_context)
    first = [_entry('a' * 40), _entry('b' * 40)]
    window.set_entries(first)
    window.treewidget.setCurrentItem(window.treewidget.topLevelItem(1))

    # A reload (e.g. after a refresh) keeps the same commit selected even
    # though a new commit landed at the top of the list.
    second = [_entry('c' * 40), _entry('a' * 40), _entry('b' * 40)]
    window.set_entries(second)
    assert window.selected_entry().oid == 'b' * 40
