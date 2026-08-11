"""Tests for the DAG Copy Commit actions in cola.widgets.dag.

These verify the multi-selection behaviour: selecting N commits in the DAG
list or graph and triggering "Copy Commit" / "Copy Commit (Short)" should put
N newline-separated oids on the clipboard, not just the first commit.
"""
from unittest.mock import patch

from cola.models import dag as dag_model
from cola.widgets import dag


class _FakeCommit:
    def __init__(self, oid, generation):
        self.oid = oid
        self.generation = generation


class _FakeItem:
    def __init__(self, commit):
        self.commit = commit


class _Viewer(dag.ViewerMixin):
    """Drive ViewerMixin directly with a stubbed selection.

    Avoids building a real Qt widget so the test stays deterministic in the
    full suite (see CLAUDE.local.md "Prefer deterministic widget tests").
    """

    def __init__(self, items, clicked=None):
        super().__init__()
        self.context = object()
        self.clicked = clicked
        self._items = items

    def selected_items(self):
        return self._items


def _items(*specs):
    """Build fake selected items from (oid, generation) pairs."""
    return [_FakeItem(_FakeCommit(oid, gen)) for oid, gen in specs]


def test_copy_commit_joins_selected_oids_ordered_by_generation():
    viewer = _Viewer(_items(('ccc', 3), ('aaa', 1), ('bbb', 2)))
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_to_clipboard()
    set_clipboard.assert_called_once_with('aaa\nbbb\nccc')


def test_copy_commit_short_abbreviates_each_oid():
    viewer = _Viewer(_items(('a' * 40, 1), ('b' * 40, 2)))
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        with patch.object(dag.prefs, 'abbrev', return_value=7):
            viewer.copy_to_clipboard_short()
    set_clipboard.assert_called_once_with('aaaaaaa\nbbbbbbb')


def test_single_selection_has_no_trailing_newline():
    viewer = _Viewer(_items(('only', 1)))
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_to_clipboard()
    set_clipboard.assert_called_once_with('only')


def test_empty_selection_is_a_no_op():
    viewer = _Viewer([])
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_to_clipboard()
        viewer.copy_to_clipboard_short()
    set_clipboard.assert_not_called()


def test_worktree_and_stage_pseudo_commits_are_filtered_out():
    viewer = _Viewer(
        _items(
            (dag_model.WORKTREE, 3),
            (dag_model.STAGE, 2),
            ('real', 1),
        )
    )
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_to_clipboard()
    set_clipboard.assert_called_once_with('real')


def test_right_click_outside_selection_copies_only_the_clicked_commit():
    """Right-clicking a commit that is not part of the current selection copies
    just that commit, matching the long-standing single-commit behaviour."""
    selected = _items(('aaa', 1), ('bbb', 2))
    clicked = _FakeCommit('ccc', 3)
    viewer = _Viewer(selected, clicked=clicked)
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_to_clipboard()
    set_clipboard.assert_called_once_with('ccc')


def test_right_click_inside_selection_copies_the_whole_selection():
    selected = _items(('aaa', 1), ('bbb', 2))
    viewer = _Viewer(selected, clicked=selected[0].commit)
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_to_clipboard()
    set_clipboard.assert_called_once_with('aaa\nbbb')
