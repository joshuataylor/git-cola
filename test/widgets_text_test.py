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
    """Send a left-button mouse event to the widget's viewport"""
    viewport = widget.viewport()
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
