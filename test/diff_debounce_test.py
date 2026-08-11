"""Tests for the debounced, supersede-guarded diff loading in CommitDiffWidget.

Stepping commit-to-commit in the DAG must not spawn a "git diff" for every
commit passed over, and a slow diff that finishes after the selection has
moved on must not overwrite the diff for the current commit.
"""
import sys
from unittest.mock import MagicMock

import pytest

from cola.models import dag
from cola.widgets.diff import CommitDiffWidget
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


def _make_commit(oid):
    commit = MagicMock()
    commit.oid = oid
    commit.author = 'A U Thor'
    commit.email = 'author@example.com'
    commit.authdate = '2026-01-01'
    commit.summary = 'summary'
    return commit


def _make_widget(app_context):
    widget = CommitDiffWidget(app_context, None, is_commit=True)
    # Observe diff task scheduling without running real git.
    app_context.runtask = MagicMock()
    return widget


def test_commits_selected_debounces_diff_load(qapp, app_context):
    """Rapid selection changes only load the diff for the settled-on commit."""
    widget = _make_widget(app_context)

    # Step through three commits faster than the debounce interval.
    for oid in ('a' * 40, 'b' * 40, 'c' * 40):
        widget.commits_selected([_make_commit(oid)])

    # No diff task has been started yet -- only the timer is pending.
    app_context.runtask.start.assert_not_called()
    assert widget._pending_diff == ('oid', 'c' * 40)
    # Metadata still tracks the latest selection for immediate responsiveness.
    assert widget.oid == 'c' * 40

    # Fire the debounce as the event loop eventually would.
    widget._load_pending_diff()
    assert app_context.runtask.start.call_count == 1
    assert widget._pending_diff is None


def test_empty_selection_cancels_pending_diff(qapp, app_context):
    """Clearing the selection drops any pending diff load."""
    widget = _make_widget(app_context)

    widget.commits_selected([_make_commit('a' * 40)])
    assert widget._pending_diff is not None

    widget.commits_selected([])
    assert widget._pending_diff is None
    assert not widget._diff_timer.isActive()
    app_context.runtask.start.assert_not_called()


def test_set_diff_drops_superseded_result(qapp, app_context):
    """A result from a superseded task is discarded; the latest one applies."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()

    # Two diffs started in sequence: a stale token (1) then the current one (2).
    stale_token = 1
    current_token = 2
    widget._diff_token = current_token

    # The stale result arrives late and must be ignored.
    widget.set_diff('stale diff', stale_token)
    widget.diff.set_diff.assert_not_called()

    # The current result is applied.
    widget.set_diff('current diff', current_token)
    widget.diff.set_diff.assert_called_once_with('current diff')


def test_set_diff_without_token_applies_immediately(qapp, app_context):
    """Direct callers (e.g. graph diff) pass no token and are never dropped."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()

    widget.set_diff('direct diff')
    widget.diff.set_diff.assert_called_once_with('direct diff')


def test_first_commit_starts_at_top(qapp, app_context):
    """The first diff shown has no remembered position, so it starts at top."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()

    widget.set_diff_oid('a' * 40)
    # No prior diff, so nothing is saved and the target is the top (None).
    widget.diff.set_scrollbar_target.assert_called_once_with(None)
    widget.diff.save_scrollbar.assert_not_called()


def test_same_commit_rerender_preserves_scrollbar(qapp, app_context):
    """Re-rendering the same diff (e.g. word-wrap toggle) keeps the scroll."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()

    # Show a commit, then re-load the identical diff key.
    widget.set_diff_oid('a' * 40)
    widget.diff.save_scrollbar.reset_mock()
    widget.diff.set_scrollbar_target.reset_mock()

    widget.set_diff_oid('a' * 40)
    widget.diff.save_scrollbar.assert_called_once()
    widget.diff.set_scrollbar_target.assert_not_called()


def test_switching_commit_remembers_and_restores_position(qapp, app_context):
    """Each commit's scroll position is remembered and restored on return."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()

    # Commit A is shown and scrolled to 200.
    widget.set_diff_oid('a' * 40)
    widget.diff.scrollbar_value.return_value = 200

    # Switch to B: A's position (200) is captured, B is unseen -> top (None).
    widget.set_diff_oid('b' * 40)
    widget.diff.set_scrollbar_target.assert_called_with(None)
    assert widget._scroll_positions[('a' * 40, None)] == 200
    widget.diff.scrollbar_value.return_value = 50  # B gets scrolled to 50

    # Back to A: B's position (50) is captured and A's (200) is restored.
    widget.set_diff_oid('a' * 40)
    widget.diff.set_scrollbar_target.assert_called_with(200)
    assert widget._scroll_positions[('b' * 40, None)] == 50


def test_file_within_commit_is_separate_position(qapp, app_context):
    """A different file in the same commit has its own remembered position."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()

    widget.set_diff_oid('a' * 40)
    widget.diff.scrollbar_value.return_value = 123

    # Same oid, different filename -> different diff key -> its own (top) target.
    widget.set_diff_oid('a' * 40, filename='some/file.py')
    widget.diff.set_scrollbar_target.assert_called_with(None)
    assert widget._scroll_positions[('a' * 40, None)] == 123


def test_set_diff_routes_ansi_to_external_renderer(qapp, app_context):
    """ANSI output is rendered via set_ansi_diff when a DAG diff command is set."""
    from unittest.mock import patch

    widget = _make_widget(app_context)
    widget.diff = MagicMock()
    ansi_diff = '\x1b[31mext\x1b[0m'

    with patch('cola.widgets.diff.prefs.dag_diff_command', return_value='difft'):
        widget.set_diff(ansi_diff)

    widget.diff.set_ansi_diff.assert_called_once_with(ansi_diff)
    widget.diff.set_diff.assert_not_called()


def test_set_diff_uses_plain_renderer_without_command(qapp, app_context):
    """Without a DAG diff command, output goes through the normal renderer."""
    from unittest.mock import patch

    widget = _make_widget(app_context)
    widget.diff = MagicMock()
    ansi_diff = '\x1b[31mext\x1b[0m'

    with patch('cola.widgets.diff.prefs.dag_diff_command', return_value=''):
        widget.set_diff(ansi_diff)

    widget.diff.set_diff.assert_called_once_with(ansi_diff)
    widget.diff.set_ansi_diff.assert_not_called()


def test_revisiting_commit_serves_diff_from_cache(qapp, app_context):
    """Returning to a commit renders from cache without re-running git."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()
    oid = 'a' * 40

    # First visit: cache miss -> a diff task is started.
    widget.commits_selected([_make_commit(oid)])
    widget._load_pending_diff()
    assert app_context.runtask.start.call_count == 1
    # Simulate the background task returning its result.
    widget.set_diff('diff A', widget._diff_token)
    assert widget._diff_cache[(oid, None)] == 'diff A'

    # Second visit: cache hit -> rendered synchronously, no new task.
    widget.diff.set_diff.reset_mock()
    widget.commits_selected([_make_commit(oid)])
    widget._load_pending_diff()
    assert app_context.runtask.start.call_count == 1  # unchanged
    widget.diff.set_diff.assert_called_once_with('diff A')


def test_pseudo_commit_diff_is_not_cached(qapp, app_context):
    """Volatile WORKTREE/STAGE diffs are never cached."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()

    widget.commits_selected([_make_commit(dag.WORKTREE)])
    widget._load_pending_diff()
    widget.set_diff('worktree diff', widget._diff_token)

    assert (dag.WORKTREE, None) not in widget._diff_cache
    assert len(widget._diff_cache) == 0


def test_clear_diff_cache_empties_cache(qapp, app_context):
    """clear_diff_cache drops all cached diffs (called when the DAG reloads)."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()
    oid = 'a' * 40

    widget.commits_selected([_make_commit(oid)])
    widget._load_pending_diff()
    widget.set_diff('diff A', widget._diff_token)
    assert widget._diff_cache

    widget.clear_diff_cache()
    assert not widget._diff_cache


def test_diff_cache_evicts_oldest_beyond_cap(qapp, app_context):
    """The cache is bounded; the least-recently-used entry is evicted."""
    widget = _make_widget(app_context)
    widget.diff = MagicMock()
    cap = widget._DIFF_CACHE_MAX

    for i in range(cap + 5):
        oid = f'{i:040d}'
        widget._displayed_diff_key = (oid, None)
        widget._diff_token += 1
        widget.set_diff(f'diff {i}', widget._diff_token)

    assert len(widget._diff_cache) == cap
    # The earliest entries were evicted; the most recent are retained.
    assert (f'{0:040d}', None) not in widget._diff_cache
    assert (f'{cap + 4:040d}', None) in widget._diff_cache
