"""Tests for the DAG Copy Commit actions in cola.widgets.dag.

These verify the multi-selection behaviour: selecting N commits in the DAG
list or graph and triggering "Copy Commit" / "Copy Commit (Short)" should put
N newline-separated oids on the clipboard, not just the first commit.
"""
from unittest.mock import patch

from cola.models import dag as dag_model
from cola.widgets import dag


class _FakeCommit:
    def __init__(self, oid, generation, summary=''):
        self.oid = oid
        self.generation = generation
        self.summary = summary or f'title {oid}'


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


MESSAGES = {
    'aaa': 'subject one\n\nbody that\nwraps.\n\nSigned-off-by: X <x@y>\n',
    'bbb': 'subject two\n',
}


def _fake_log(_context, _count, oid, *_args, **_kwargs):
    return MESSAGES[oid]


def test_copy_commit_message_keeps_wrapping():
    viewer = _Viewer(_items(('aaa', 1)))
    with patch.object(dag.gitcmds, 'log', side_effect=_fake_log):
        with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
            viewer.copy_message_to_clipboard()
    set_clipboard.assert_called_once_with(
        'subject one\n\nbody that\nwraps.\n\nSigned-off-by: X <x@y>'
    )


def test_copy_commit_message_unwrapped_joins_body_only():
    viewer = _Viewer(_items(('aaa', 1)))
    with patch.object(dag.gitcmds, 'log', side_effect=_fake_log):
        with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
            viewer.copy_message_unwrapped_to_clipboard()
    set_clipboard.assert_called_once_with(
        'subject one\n\nbody that wraps.\n\nSigned-off-by: X <x@y>'
    )


def test_copy_commit_message_joins_multiple_selection_in_generation_order():
    viewer = _Viewer(_items(('bbb', 2), ('aaa', 1)))
    with patch.object(dag.gitcmds, 'log', side_effect=_fake_log):
        with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
            viewer.copy_message_unwrapped_to_clipboard()
    set_clipboard.assert_called_once_with(
        'subject one\n\nbody that wraps.\n\nSigned-off-by: X <x@y>\n\nsubject two'
    )


def test_copy_commit_message_skips_pseudo_commits_and_empty_selection():
    viewer = _Viewer(_items((dag_model.WORKTREE, 2), (dag_model.STAGE, 1)))
    with patch.object(dag.gitcmds, 'log', side_effect=_fake_log) as log:
        with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
            viewer.copy_message_to_clipboard()
            viewer.copy_message_unwrapped_to_clipboard()
    log.assert_not_called()
    set_clipboard.assert_not_called()


def test_copy_commit_title_joins_titles_in_generation_order():
    viewer = _Viewer(_items(('bbb', 2), ('aaa', 1)))
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_title_to_clipboard()
    set_clipboard.assert_called_once_with('title aaa\ntitle bbb')


def test_copy_commit_title_uses_the_clicked_commit_outside_the_selection():
    clicked = _FakeCommit('ccc', 3, summary='clicked title')
    viewer = _Viewer(_items(('aaa', 1)), clicked=clicked)
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_title_to_clipboard()
    set_clipboard.assert_called_once_with('clicked title')


def test_copy_commit_title_skips_pseudo_commits():
    viewer = _Viewer(_items((dag_model.WORKTREE, 2), (dag_model.STAGE, 1)))
    with patch.object(dag.qtutils, 'set_clipboard') as set_clipboard:
        viewer.copy_title_to_clipboard()
    set_clipboard.assert_not_called()
