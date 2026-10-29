"""Tests for the diff syntax highlighter's intra-line span handling.

Setting a diff already highlights the document once; applying intra-line spans
must not trigger a second full-document rehighlight unless the spans actually
change.
"""
import sys

import pytest

from cola.widgets.diff import DiffSyntaxHighlighter
from qtpy import QtGui
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


def _make_highlighter(app_context):
    doc = QtGui.QTextDocument()
    highlighter = DiffSyntaxHighlighter(app_context, doc)
    calls = []
    # Observe rehighlight without running the (expensive) real pass.
    highlighter.rehighlight = lambda: calls.append(1)
    return highlighter, calls


def test_set_intraline_spans_skips_rehighlight_when_empty(qapp, app_context):
    """Empty spans on an already-empty highlighter do not rehighlight."""
    highlighter, calls = _make_highlighter(app_context)

    highlighter.set_intraline_spans({})
    assert calls == []
    highlighter.set_intraline_spans(None)
    assert calls == []


def test_set_intraline_spans_rehighlights_on_change(qapp, app_context):
    """A real change to the spans triggers exactly one rehighlight."""
    highlighter, calls = _make_highlighter(app_context)

    spans = {0: [(0, 1)]}
    highlighter.set_intraline_spans(spans)
    assert len(calls) == 1

    # The same spans again is a no-op; the document already reflects them.
    highlighter.set_intraline_spans(dict(spans))
    assert len(calls) == 1

    # Clearing back to empty is a change, so it rehighlights once.
    highlighter.set_intraline_spans({})
    assert len(calls) == 2


def test_clear_intraline_spans_does_not_rehighlight(qapp, app_context):
    """clear_intraline_spans resets state without a rehighlight pass."""
    highlighter, calls = _make_highlighter(app_context)
    highlighter.set_intraline_spans({0: [(0, 1)]})
    assert len(calls) == 1

    highlighter.clear_intraline_spans()
    assert highlighter._intraline_spans == {}
    assert len(calls) == 1  # unchanged: no extra pass


def test_preset_intraline_spans_does_not_rehighlight(qapp, app_context):
    """Spans for the next document are stored without a highlight pass"""
    highlighter, calls = _make_highlighter(app_context)
    highlighter.preset_intraline_spans({3: [(0, 1)]})
    assert highlighter._intraline_spans == {3: [(0, 1)]}
    assert calls == []


def _diff_text_edit(app_context):
    from cola.widgets.diff import DiffTextEdit

    widget = DiffTextEdit(app_context, None)
    calls = []
    widget.highlighter.rehighlight = lambda: calls.append(1)
    return widget, calls


def test_set_diff_highlights_once_with_intraline_spans(qapp, app_context):
    """The spans are in place before setPlainText, so no second pass runs"""
    widget, calls = _diff_text_edit(app_context)
    diff = '@@ -1 +1 @@\n-total = compute_total(items, tax=0.10)\n+total = compute_total(items, tax=0.15)\n'
    widget.set_diff(diff)
    assert widget.highlighter._intraline_spans
    assert calls == []


def test_huge_diffs_skip_intraline_spans(qapp, app_context, monkeypatch):
    from cola.widgets import diff as diff_module

    monkeypatch.setattr(diff_module, 'INTRALINE_DIFF_MAX_LINES', 2)
    widget, _calls = _diff_text_edit(app_context)
    widget.set_diff(
        '@@ -1 +1 @@\n-total = compute_total(items, tax=0.10)\n+total = compute_total(items, tax=0.15)\n'
    )
    assert widget.highlighter._intraline_spans == {}


_DIFF = '@@ -1 +1 @@\n-total = compute_total(items, tax=0.10)\n+total = compute_total(items, tax=0.15)\n'


def test_set_diff_skips_an_identical_diff(qapp, app_context):
    """A refresh that re-diffs an unchanged file does not render it again"""
    from unittest.mock import patch

    edit, _calls = _diff_text_edit(app_context)
    edit.set_diff(_DIFF)
    with patch.object(edit, '_compute_intraline_spans', return_value={}) as compute:
        edit.set_diff(_DIFF)
        compute.assert_not_called()
        edit.set_diff(_DIFF.replace('0.15', '0.20'))
        assert compute.call_count == 1
    assert '0.20' in edit.toPlainText()


def test_set_diff_renders_again_after_another_write(qapp, app_context):
    """An ANSI diff or other text in between forces a real render"""
    edit, _calls = _diff_text_edit(app_context)
    edit.set_diff(_DIFF)
    edit.set_ansi_diff('plain text')
    assert edit.toPlainText() == 'plain text'
    edit.set_diff(_DIFF)
    assert edit.toPlainText() == _DIFF.rstrip('\n')
    assert edit.highlighter.enabled
    edit.clear()
    edit.set_diff(_DIFF)
    assert edit.toPlainText() == _DIFF.rstrip('\n')


def _bad_whitespace_start(app_context, line):
    """Return where the bad-whitespace format starts on an added line, or None"""
    from cola.widgets.diff import DiffTextEdit

    edit = DiffTextEdit(app_context, None)
    edit.set_diff('@@ -1 +1 @@\n-old\n' + line + '\n')
    block = edit.document().findBlockByNumber(2)
    bad = edit.highlighter.bad_whitespace_fmt.background()
    starts = [
        fmt.start for fmt in block.layout().formats() if fmt.format.background() == bad
    ]
    return starts[0] if starts else None


def test_added_lines_mark_only_their_trailing_whitespace(qapp, app_context):
    """Long padded runs mid-line, as in Markdown tables, are not trailing"""
    padded = '+| a' + ' ' * 3000 + '| b' + ' ' * 2000 + '|'
    assert _bad_whitespace_start(app_context, padded) is None
    assert _bad_whitespace_start(app_context, padded + ' \t ') == len(padded)
    assert _bad_whitespace_start(app_context, '+text') is None
    assert _bad_whitespace_start(app_context, '+text ') == len('+text')


class _HeldRunTask:
    """Hold started tasks until the test finishes them"""

    def __init__(self):
        self.started = []

    def start(self, task, result=None):
        self.started.append((task, result))

    def finish(self, index=0):
        task, result = self.started.pop(index)
        result(task.task())


def _deferring_edit(app_context, monkeypatch):
    from cola.widgets import diff as diff_module

    monkeypatch.setattr(diff_module, 'INTRALINE_DIFF_SYNC_CHARS', 0)
    edit, calls = _diff_text_edit(app_context)
    edit._intraline_runtask = _HeldRunTask()
    return edit, calls


def test_large_diffs_show_at_once_and_get_their_spans_later(
    qapp, app_context, monkeypatch
):
    edit, calls = _deferring_edit(app_context, monkeypatch)
    edit.set_diff(_DIFF)
    assert edit.toPlainText() == _DIFF.rstrip('\n')
    assert edit.highlighter._intraline_spans == {}
    assert edit._intraline_timer.isActive()

    edit._intraline_timer.stop()
    edit._start_deferred_intraline()  # The timer firing.
    edit._intraline_runtask.finish()
    assert edit.highlighter._intraline_spans
    assert calls == [1]  # One rehighlight applies them.


def test_deferred_spans_for_a_replaced_diff_are_dropped(qapp, app_context, monkeypatch):
    edit, calls = _deferring_edit(app_context, monkeypatch)
    edit.set_diff(_DIFF)
    edit._start_deferred_intraline()
    edit.set_diff(_DIFF.replace('0.15', '0.20'))
    edit._intraline_runtask.finish()  # The first diff's result.
    assert edit.highlighter._intraline_spans == {}
    assert calls == []


def test_changing_the_preset_cancels_a_pending_deferred_compute(
    qapp, app_context, monkeypatch
):
    edit, _calls = _deferring_edit(app_context, monkeypatch)
    edit.set_diff(_DIFF)
    edit._start_deferred_intraline()
    edit.set_intraline_diff_preset('line_only')
    assert not edit._intraline_timer.isActive()
    edit._intraline_runtask.finish()  # Computed with the old preset.
    assert edit.highlighter._intraline_spans == {}


def test_small_diffs_still_get_their_spans_in_the_first_pass(qapp, app_context):
    edit, calls = _diff_text_edit(app_context)
    edit.set_diff(_DIFF)
    assert edit.highlighter._intraline_spans
    assert not edit._intraline_timer.isActive()
    assert calls == []
