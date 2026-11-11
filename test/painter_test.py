"""Painters must be ended explicitly, not left to end when their object is freed

A QPainter (or QStylePainter) on a widget that is never ended stays active for
as long as anything holds the frame that created it: a sampling profiler such
as scalene, a debugger or a tracer. Qt then flushes the window under an active
painter, which crashed in QBackingStore::flush() on macOS.
"""
import ast
import os
import sys
from unittest.mock import MagicMock

import pytest

from cola.widgets import archive
from cola.widgets import text
from cola.widgets.diff import DiffLineNumbers
from qtpy import QtCore
from qtpy import QtWidgets

PAINTER_CLASSES = ('QPainter', 'QStylePainter')
ACTIVE_PAINTER_WARNING = 'called with active painter'


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for the widget tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


def _painter_name(node):
    """The variable a `name = QPainter(device)` statement assigns, if any"""
    if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
        return None
    func = node.value.func
    func_name = (
        func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
    )
    if func_name not in PAINTER_CLASSES or not node.value.args:
        return None
    if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def _ends_or_returns(function, name):
    """Does the function call `name.end()` or hand the painter to its caller?"""
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'end'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
        ):
            return True
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id == name
        ):
            return True
    return False


def _unended_painters():
    """Yield (path:line, function) for each painter in cola/ that is never ended"""
    root = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cola')
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, encoding='utf-8') as handle:
                tree = ast.parse(handle.read(), filename=path)
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    name = _painter_name(node)
                    if name and not _ends_or_returns(function, name):
                        where = f'{os.path.relpath(path, root)}:{node.lineno}'
                        yield where, function.name


def test_every_painter_is_ended_explicitly():
    """Each painter is ended in the function that creates it, or returned"""
    assert list(_unended_painters()) == []


class _HeldFrames:
    """Keep every paintEvent frame alive, as a sampling profiler does"""

    def __init__(self):
        self.frames = []

    def __call__(self, frame, event, _arg):
        if event == 'call' and frame.f_code.co_name == 'paintEvent':
            self.frames.append(frame)


def _paint_with_frames_held(qapp, widget):
    """Show and repaint a widget with its paintEvent frames held

    Returns the Qt warnings about active painters that were logged.
    """
    messages = []
    previous = QtCore.qInstallMessageHandler(
        lambda _mode, _context, message: messages.append(message)
    )
    held = _HeldFrames()
    try:
        widget.resize(200, 120)
        widget.show()
        qapp.processEvents()
        sys.setprofile(held)
        try:
            widget.repaint()
            qapp.processEvents()
        finally:
            sys.setprofile(None)
        assert held.frames, 'paintEvent() never ran'
    finally:
        QtCore.qInstallMessageHandler(previous)
        widget.close()
        held.frames.clear()
    return [message for message in messages if ACTIVE_PAINTER_WARNING in message]


def test_line_numbers_end_their_painter_with_frames_held(qapp):
    editor = QtWidgets.QPlainTextEdit()
    editor.setPlainText('one\ntwo\nthree\n')
    numbers = text.LineNumbers(editor)
    numbers.refresh_size()
    assert _paint_with_frames_held(qapp, editor) == []


def test_diff_line_numbers_end_their_painter_with_frames_held(qapp):
    context = MagicMock()
    context.cfg.get.return_value = None
    editor = QtWidgets.QPlainTextEdit()
    diff = '@@ -1,2 +1,2 @@\n one\n-two\n+three\n'
    editor.setPlainText(diff)
    numbers = DiffLineNumbers(context, editor)
    numbers.set_diff(diff)
    numbers.resize(60, 120)
    assert _paint_with_frames_held(qapp, editor) == []


def test_expandable_group_box_ends_its_painter_with_frames_held(qapp):
    box = archive.ExpandableGroupBox()
    box.setTitle('Advanced')
    assert _paint_with_frames_held(qapp, box) == []
