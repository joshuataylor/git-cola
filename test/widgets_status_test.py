"""Tests for selection save/restore in cola.widgets.status.StatusTreeWidget.

The model delivers its about_to_update/updated signals with Qt.QueuedConnection,
so by the time the widget's _save_selection() slot runs the model has already
replaced its file lists. These tests reproduce that ordering deterministically
(without git, background tasks or the command machinery) to pin the behaviour of
_save_selection()/_restore_selection().
"""
import sys
from unittest.mock import MagicMock

import pytest

from cola.models import selection
from cola.widgets import status
from qtpy import QtWidgets


@pytest.fixture(scope='module')
def qapp():
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(sys.argv[:1] if sys.argv else ['test'])
    yield instance


class _FakeModel:
    """The handful of MainModel attributes the status tree reads."""

    def __init__(self):
        self.staged = []
        self.unmerged = []
        self.modified = []
        self.untracked = []
        self.staged_deleted = set()
        self.unstaged_deleted = set()

    def set_contents(self, staged=(), unmerged=(), modified=(), untracked=()):
        self.staged = list(staged)
        self.unmerged = list(unmerged)
        self.modified = list(modified)
        self.untracked = list(untracked)


@pytest.fixture
def widget(qapp):
    tree = status.StatusTreeWidget(MagicMock(), None)
    tree._model = _FakeModel()
    return tree


def _untracked_child_paths(widget):
    parent = widget.topLevelItem(status.UNTRACKED_IDX)
    return [parent.child(i).text(0) for i in range(parent.childCount())]


def _selected_untracked(widget):
    parent = widget.topLevelItem(status.UNTRACKED_IDX)
    return [
        parent.child(i).text(0)
        for i in range(parent.childCount())
        if parent.child(i).isSelected()
    ]


def _select_untracked(widget, names, current):
    parent = widget.topLevelItem(status.UNTRACKED_IDX)
    for i in range(parent.childCount()):
        if parent.child(i).text(0) == current:
            widget.setCurrentItem(parent.child(i))
    for i in range(parent.childCount()):
        if parent.child(i).text(0) in names:
            parent.child(i).setSelected(True)


def _stage_untracked(widget, staged, remaining):
    """Replay the queued signal sequence for staging untracked files.

    In production the model emits previous_contents (old lists) then
    about_to_update, swaps in the new lists, and finally emits updated -- all
    queued, so the slots run after the swap. This mirrors that: capture the old
    lists in previous_contents, swap the model to the post-stage state, run the
    (delayed) _save_selection(), then refresh() as the updated slot would.
    """
    old_untracked = list(widget._model.untracked)
    widget.previous_contents = selection.State([], [], [], old_untracked)
    widget._model.set_contents(staged=staged, untracked=remaining)
    widget._save_selection()
    widget.refresh()


def test_staging_contiguous_untracked_selects_only_the_next_file(widget):
    """Staging a contiguous block leaves only the single following file selected.

    Regression: _save_selection() ran after the model swapped in the new lists,
    so it mapped the still-old selected rows onto the new path list. Staging
    a,b,c recorded d,e as the previous selection and _restore_selection()
    reselected both instead of just d.
    """
    widget._model.set_contents(untracked=['a', 'b', 'c', 'd', 'e'])
    widget.refresh()
    assert _untracked_child_paths(widget) == ['a', 'b', 'c', 'd', 'e']

    _select_untracked(widget, {'a', 'b', 'c'}, current='c')
    _stage_untracked(widget, staged=['a', 'b', 'c'], remaining=['d', 'e'])

    assert _untracked_child_paths(widget) == ['d', 'e']
    assert _selected_untracked(widget) == ['d']


def test_staging_trailing_untracked_selects_the_previous_file(widget):
    """Staging the last files falls back to the nearest preceding survivor."""
    widget._model.set_contents(untracked=['a', 'b', 'c', 'd', 'e'])
    widget.refresh()

    _select_untracked(widget, {'c', 'd', 'e'}, current='e')
    _stage_untracked(widget, staged=['c', 'd', 'e'], remaining=['a', 'b'])

    assert _untracked_child_paths(widget) == ['a', 'b']
    assert _selected_untracked(widget) == ['b']


def test_staging_all_untracked_leaves_nothing_selected(widget):
    """Staging every untracked file clears the section and its selection."""
    widget._model.set_contents(untracked=['a', 'b', 'c'])
    widget.refresh()

    _select_untracked(widget, {'a', 'b', 'c'}, current='b')
    _stage_untracked(widget, staged=['a', 'b', 'c'], remaining=[])

    assert _untracked_child_paths(widget) == []
    assert _selected_untracked(widget) == []


def _children(widget, idx):
    parent = widget.topLevelItem(idx)
    return [parent.child(i) for i in range(parent.childCount())]


def test_unchanged_sections_keep_their_items(widget):
    """A refresh only rebuilds sections whose (path, deleted) contents changed."""
    widget._model.set_contents(modified=['m1'], untracked=['u1', 'u2'])
    widget.refresh()
    untracked_before = _children(widget, status.UNTRACKED_IDX)
    modified_before = _children(widget, status.MODIFIED_IDX)

    # Identical contents: every section keeps its item objects.
    widget.refresh()
    assert all(
        a is b
        for a, b in zip(_children(widget, status.UNTRACKED_IDX), untracked_before)
    )
    assert all(
        a is b for a, b in zip(_children(widget, status.MODIFIED_IDX), modified_before)
    )

    # Growing Modified rebuilds it but leaves Untracked untouched.
    widget._model.set_contents(modified=['m1', 'm2'], untracked=['u1', 'u2'])
    widget.refresh()
    untracked_after = _children(widget, status.UNTRACKED_IDX)
    assert len(untracked_after) == 2
    assert all(a is b for a, b in zip(untracked_after, untracked_before))
    assert [item.path for item in _children(widget, status.MODIFIED_IDX)] == [
        'm1',
        'm2',
    ]


def test_deleted_flag_change_rebuilds_the_section(widget):
    """The same path list with a changed deleted flag still rebuilds."""
    widget._model.set_contents(modified=['m1'])
    widget.refresh()
    item = _children(widget, status.MODIFIED_IDX)[0]
    assert not item.deleted

    widget._model.unstaged_deleted = {'m1'}
    widget.refresh()
    new_item = _children(widget, status.MODIFIED_IDX)[0]
    assert new_item is not item
    assert new_item.deleted


def test_unchanged_refresh_preserves_the_selection(widget):
    """A no-op refresh leaves the live selection and current item alone."""
    widget._model.set_contents(untracked=['a', 'b', 'c'])
    widget.refresh()
    _select_untracked(widget, {'b'}, current='b')

    widget.previous_contents = selection.State([], [], [], ['a', 'b', 'c'])
    widget._save_selection()
    widget.refresh()

    assert _selected_untracked(widget) == ['b']
    parent = widget.topLevelItem(status.UNTRACKED_IDX)
    assert widget.currentItem() is parent.child(1)


def test_content_only_refresh_recomputes_current_diff(widget):
    """Editing an already-listed file re-diffs it even when no section rebuilds.

    Regression from ee1f75f0: a file's on-disk content can change without
    altering its (path, deleted) fingerprint, so no section rebuilds,
    _restore_selection() is skipped, and the diff pane never updates. A no-op
    refresh must still dispatch the diff for the current selection.
    show_selection() runs it through runtask.run().
    """
    widget._model.set_contents(modified=['m1'])
    widget.refresh()
    modified = widget.topLevelItem(status.MODIFIED_IDX)
    widget.setCurrentItem(modified.child(0))
    modified.child(0).setSelected(True)

    widget.context.runtask.run.reset_mock()
    widget.previous_contents = selection.State([], [], ['m1'], [])
    widget._save_selection()
    widget.refresh()  # identical contents -> no section rebuilt

    assert widget.context.runtask.run.called


def test_content_only_refresh_without_selection_skips_diff(widget):
    """A no-op refresh with nothing selected does not dispatch a diff."""
    widget._model.set_contents(modified=['m1'])
    widget.refresh()
    widget.setCurrentItem(None)

    widget.context.runtask.run.reset_mock()
    widget.previous_contents = selection.State([], [], ['m1'], [])
    widget._save_selection()
    widget.refresh()

    assert not widget.context.runtask.run.called


def test_edit_with_other_section_change_recomputes_current_diff(widget):
    """A change in one section re-diffs a current selection whose own section
    was not rebuilt.

    Adding an untracked file rebuilds Untracked but leaves Modified untouched;
    reselecting the already-current Modified item emits no itemSelectionChanged,
    so the diff must be recomputed explicitly.
    """
    widget._model.set_contents(modified=['m1'])
    widget.refresh()
    modified = widget.topLevelItem(status.MODIFIED_IDX)
    widget.setCurrentItem(modified.child(0))
    modified.child(0).setSelected(True)

    widget.context.runtask.run.reset_mock()
    widget.previous_contents = selection.State([], [], ['m1'], [])
    widget._save_selection()
    widget._model.set_contents(modified=['m1'], untracked=['u1'])
    widget.refresh()

    assert widget.context.runtask.run.called


def _selected_children(widget, idx):
    parent = widget.topLevelItem(idx)
    return [
        parent.child(i).text(0)
        for i in range(parent.childCount())
        if parent.child(i).isSelected()
    ]


def test_select_all_selects_every_category_and_focuses_first_file(widget):
    """Select All selects every file across all categories, current on the first."""
    widget._model.set_contents(modified=['m1', 'm2', 'm3'], untracked=['u1', 'u2'])
    widget.refresh()

    modified = widget.topLevelItem(status.MODIFIED_IDX)
    widget.setCurrentItem(modified.child(modified.childCount() - 1))

    widget.selectAll()

    assert _selected_children(widget, status.MODIFIED_IDX) == ['m1', 'm2', 'm3']
    assert _selected_children(widget, status.UNTRACKED_IDX) == ['u1', 'u2']
    # Current item is the first file of the first non-empty category, so the
    # native Down arrow navigates the selection instead of skipping past it.
    assert widget.currentItem() is modified.child(0)


def test_select_all_from_header_selects_every_category(widget):
    """Select All while a header is current still selects every category."""
    widget._model.set_contents(modified=['m1', 'm2'], untracked=['u1'])
    widget.refresh()

    modified = widget.topLevelItem(status.MODIFIED_IDX)
    widget.setCurrentItem(modified)

    widget.selectAll()

    assert _selected_children(widget, status.MODIFIED_IDX) == ['m1', 'm2']
    assert _selected_children(widget, status.UNTRACKED_IDX) == ['u1']
    assert widget.currentItem() is modified.child(0)


def test_select_all_with_no_current_item_selects_every_category(widget):
    """With nothing focused, Select All still selects every category."""
    widget._model.set_contents(modified=['m1', 'm2'], untracked=['u1'])
    widget.refresh()
    widget.setCurrentItem(None)

    widget.selectAll()

    modified = widget.topLevelItem(status.MODIFIED_IDX)
    assert _selected_children(widget, status.MODIFIED_IDX) == ['m1', 'm2']
    assert _selected_children(widget, status.UNTRACKED_IDX) == ['u1']
    assert widget.currentItem() is modified.child(0)


def test_select_all_with_empty_tree_is_a_safe_noop(widget):
    """Select All on an empty tree returns without selecting anything."""
    widget._model.set_contents()
    widget.refresh()

    widget.selectAll()

    assert _selected_children(widget, status.MODIFIED_IDX) == []
    assert _selected_children(widget, status.UNTRACKED_IDX) == []
