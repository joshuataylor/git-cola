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
