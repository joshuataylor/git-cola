"""Tests for mouse selection in the text widgets (cola.widgets.text).

Mouse events are sent straight to the viewport the way Qt6 delivers them: the
press that forms a double-click arrives only as MouseButtonDblClick. The
sequences run without waits, so they stay inside the double-click interval.
"""
import sys

import pytest

from cola.widgets import text
from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets
from qtpy.QtCore import QPoint
from qtpy.QtCore import QPointF
from qtpy.QtCore import Qt
from qtpy.QtTest import QTest

LINE = '+    some_function(alpha, beta, gamma)'
TEXT = f'first line here\n{LINE}\nthird line\n'
# QTextCursor.selectedText() ends each selected block with U+2029.
PARAGRAPH_SEPARATOR = chr(0x2029)
WHOLE_LINE = LINE + PARAGRAPH_SEPARATOR

PRESS = QtCore.QEvent.MouseButtonPress
RELEASE = QtCore.QEvent.MouseButtonRelease
DOUBLE_CLICK = QtCore.QEvent.MouseButtonDblClick
MOVE = QtCore.QEvent.MouseMove


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for the widget tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


def _make_widget(cls):
    widget = cls(readonly=True)
    widget.setPlainText(TEXT)
    widget.resize(600, 200)
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    return widget


@pytest.fixture(params=[text.PlainTextEdit, text.TextEdit])
def widget(qapp, request):
    instance = _make_widget(request.param)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def plain_widget(qapp):
    instance = _make_widget(text.PlainTextEdit)
    try:
        yield instance
    finally:
        instance.close()


def _point(widget, column, line=1):
    """Return the viewport position of a column on a line"""
    cursor = QtGui.QTextCursor(widget.document().findBlockByNumber(line))
    cursor.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.MoveAnchor, column)
    rect = widget.cursorRect(cursor)
    return QPoint(rect.left() + 1, rect.center().y())


def _send(widget, event_type, pos):
    """Send a left-button mouse event to the widget (or its viewport)"""
    if hasattr(widget, 'viewport'):
        viewport = widget.viewport()
    else:
        viewport = widget
    if event_type == MOVE:
        button = Qt.NoButton
    else:
        button = Qt.LeftButton
    if event_type == RELEASE:
        buttons = Qt.NoButton
    else:
        buttons = Qt.LeftButton
    event = QtGui.QMouseEvent(
        event_type,
        QPointF(pos),
        QPointF(viewport.mapToGlobal(pos)),
        button,
        buttons,
        Qt.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(viewport, event)


def _click(widget, pos):
    _send(widget, PRESS, pos)
    _send(widget, RELEASE, pos)


def _double_click(widget, pos):
    _click(widget, pos)
    _send(widget, DOUBLE_CLICK, pos)
    _send(widget, RELEASE, pos)


def _triple_click(widget, pos):
    _double_click(widget, pos)
    _click(widget, pos)


def _selected(widget):
    return widget.textCursor().selectedText()


def test_triple_click_selects_line(widget):
    _triple_click(widget, _point(widget, 8))
    assert _selected(widget) == WHOLE_LINE


def test_double_click_drag_extends_by_word(widget):
    start = _point(widget, 8)
    _click(widget, start)
    _send(widget, DOUBLE_CLICK, start)
    _send(widget, MOVE, _point(widget, 22))
    _send(widget, RELEASE, _point(widget, 22))
    assert _selected(widget) == 'some_function(alpha'


def test_triple_click_drag_extends_by_line(plain_widget):
    widget = plain_widget
    start = _point(widget, 8)
    _double_click(widget, start)
    _send(widget, PRESS, start)
    end = _point(widget, 2, line=2)
    _send(widget, MOVE, end)
    _send(widget, RELEASE, end)
    assert _selected(widget) == WHOLE_LINE + 'third line' + PARAGRAPH_SEPARATOR


def test_press_after_intervening_click_is_not_a_triple_click(widget):
    """A click between the double-click and the next press cancels the triple-click"""
    start = _point(widget, 8)
    _double_click(widget, start)
    assert _selected(widget) == 'some_function'
    # Click elsewhere inside the selected word, which clears the selection.
    _click(widget, start + QPoint(30, 0))
    assert _selected(widget) == ''
    # Press back where the double-click was and drag a few characters.
    _send(widget, PRESS, start)
    _send(widget, MOVE, _point(widget, 11))
    _send(widget, RELEASE, _point(widget, 11))
    assert _selected(widget) == 'e_f'


def test_double_click_after_deselected_triple_click_extends_by_word(widget):
    """Clearing a line selection by clicking must not leave line-wise dragging"""
    _triple_click(widget, _point(widget, 8))
    assert _selected(widget) == WHOLE_LINE
    QTest.qWait(QtWidgets.QApplication.doubleClickInterval() + 100)
    # Click inside the selected line, which clears the selection.
    start = _point(widget, 20)
    _click(widget, start)
    assert _selected(widget) == ''
    # A quick second click is delivered as a double-click; drag from it.
    _send(widget, DOUBLE_CLICK, start)
    _send(widget, MOVE, _point(widget, 34))
    _send(widget, RELEASE, _point(widget, 34))
    assert _selected(widget) == 'alpha, beta, '


LINE_EDIT_TEXT = 'feature/stale-selection-state fix'


@pytest.fixture
def line_edit(qapp):
    instance = text.LineEdit()
    instance.setText(LINE_EDIT_TEXT)
    instance.resize(400, 30)
    instance.show()
    QTest.qWaitForWindowExposed(instance)
    try:
        yield instance
    finally:
        instance.close()


def _line_edit_point(widget, column):
    """Return the position of a column in a line edit"""
    widget.setCursorPosition(column)
    return widget.cursorRect().center()


def test_line_edit_triple_click_selects_all(line_edit):
    widget = line_edit
    _triple_click(widget, _line_edit_point(widget, 10))
    assert widget.selectedText() == LINE_EDIT_TEXT


def test_line_edit_press_after_intervening_click_is_not_a_triple_click(line_edit):
    """A click between the double-click and the next press cancels the triple-click"""
    widget = line_edit
    start = _line_edit_point(widget, 10)
    elsewhere = _line_edit_point(widget, 28)
    end = _line_edit_point(widget, 18)
    widget.deselect()
    _double_click(widget, start)
    assert widget.selectedText() == 'stale'
    _click(widget, elsewhere)
    assert widget.selectedText() == ''
    # Press back where the double-click was and drag.
    _send(widget, PRESS, start)
    _send(widget, MOVE, end)
    _send(widget, RELEASE, end)
    assert widget.selectedText() == 'ale-sele'
    # A further click still inside the interval places the cursor.
    _click(widget, start)
    assert widget.selectedText() == ''
    assert widget.cursorPosition() == 10


LABEL_TEXT = 'diff: keep the stale selection state from leaking'


@pytest.fixture
def label(qapp):
    instance = text.PlainTextLabel()
    instance.set_text(LABEL_TEXT)
    instance.resize(600, 30)
    instance.show()
    QTest.qWaitForWindowExposed(instance)
    try:
        yield instance
    finally:
        instance.close()


def _label_point(widget, column):
    """Return the position of a column in a left-aligned single-line label"""
    metrics = QtGui.QFontMetrics(widget.font())
    rect = widget.contentsRect()
    advance = metrics.horizontalAdvance(LABEL_TEXT[:column])
    return QPoint(rect.left() + advance + 2, rect.center().y())


def test_label_triple_click_selects_all(label):
    _triple_click(label, _label_point(label, 12))
    assert label.selectedText() == LABEL_TEXT


def test_label_press_after_intervening_click_is_not_a_triple_click(label):
    """A click between the double-click and the next press cancels the triple-click"""
    start = _label_point(label, 16)
    _double_click(label, start)
    assert label.selectedText() == 'stale'
    _click(label, _label_point(label, 40))
    assert label.selectedText() == ''
    _send(label, PRESS, start)
    _send(label, MOVE, _label_point(label, 24))
    _send(label, RELEASE, _label_point(label, 24))
    assert label.selectedText() == 'tale sel'


def test_label_double_click_after_deselected_triple_click_extends_by_word(label):
    """Clearing a triple-click by clicking must not leave line-wise dragging"""
    _triple_click(label, _label_point(label, 12))
    assert label.selectedText() == LABEL_TEXT
    QTest.qWait(QtWidgets.QApplication.doubleClickInterval() + 100)
    start = _label_point(label, 6)
    _click(label, _label_point(label, 30))
    assert label.selectedText() == ''
    _send(label, DOUBLE_CLICK, start)
    _send(label, MOVE, _label_point(label, 19))
    _send(label, RELEASE, _label_point(label, 19))
    assert label.selectedText() == 'keep the stale'
