"""Tests for the DAG "Unwrap commit message" display option."""
from unittest.mock import MagicMock
from unittest.mock import patch

from cola.widgets import diff

BODY = 'subject body that\nwraps.\n\n'
DIFF = 'diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n'


def test_split_commit_body_separates_body_from_diff():
    assert (BODY, DIFF) == diff.split_commit_body(BODY + DIFF)


def test_split_commit_body_without_diff_is_all_body():
    assert (BODY, '') == diff.split_commit_body(BODY)


def test_split_commit_body_without_body_is_all_diff():
    assert ('', DIFF) == diff.split_commit_body(DIFF)


def test_unwrap_commit_body_leaves_diff_untouched():
    expect = 'subject body that wraps.\n\n' + DIFF
    assert expect == diff.unwrap_commit_body(BODY + DIFF)
    assert DIFF == diff.unwrap_commit_body(DIFF)


class _Widget:
    """Drive CommitDiffWidget's rendering methods without building the widget."""

    def __init__(self):
        self.context = MagicMock()
        self.diff = MagicMock()
        self.options = MagicMock()
        self._displayed_diff = None
        self._unwrap_commit_message = False

    _render_diff = diff.CommitDiffWidget._render_diff
    set_unwrap_commit_message = diff.CommitDiffWidget.set_unwrap_commit_message


def test_toggle_rerenders_displayed_diff_from_raw_text():
    widget = _Widget()
    with patch.object(diff.prefs, 'dag_diff_command', return_value=''):
        widget._render_diff(BODY + DIFF)
        widget.diff.set_diff.assert_called_once_with(BODY + DIFF)

        widget.set_unwrap_commit_message(True, update=True)
        widget.diff.set_diff.assert_called_with('subject body that wraps.\n\n' + DIFF)
        widget.options.unwrap_commit_message.setChecked.assert_called_with(True)
        # Raw text is kept so toggling back restores the original wrapping.
        assert BODY + DIFF == widget._displayed_diff

        widget.set_unwrap_commit_message(False)
        widget.diff.set_diff.assert_called_with(BODY + DIFF)


def test_toggle_without_a_displayed_diff_does_not_render():
    widget = _Widget()
    widget.set_unwrap_commit_message(True)
    widget.diff.set_diff.assert_not_called()


def test_ansi_output_is_never_unwrapped():
    widget = _Widget()
    widget._unwrap_commit_message = True
    ansi_diff = '\x1b[31mcoloured\x1b[0m\n'
    with patch.object(diff.prefs, 'dag_diff_command', return_value='difft'):
        widget._render_diff(ansi_diff)
    widget.diff.set_ansi_diff.assert_called_once_with(ansi_diff)
    widget.diff.set_diff.assert_not_called()
