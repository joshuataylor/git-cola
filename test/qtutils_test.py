"""Tests for cola.qtutils helpers."""
import ast
import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from cola import qtutils
from qtpy import QtCore
from qtpy import QtWidgets
from qtpy.QtCore import Qt


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for the widget tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


def test_set_scroll_per_pixel_sets_both_axes(qapp):
    per_pixel = QtWidgets.QAbstractItemView.ScrollPerPixel
    tree = QtWidgets.QTreeWidget()
    # QTreeWidget defaults to per-item scrolling.
    assert tree.verticalScrollMode() != per_pixel

    qtutils.set_scroll_per_pixel(tree)

    assert tree.verticalScrollMode() == per_pixel
    assert tree.horizontalScrollMode() == per_pixel


def test_button_box_native_uses_dialog_button_box_roles(qapp, monkeypatch):
    monkeypatch.setattr(qtutils.defs, 'native_dialog_buttons', True)
    ok = qtutils.ok_button('OK')
    close = qtutils.close_button()
    other = qtutils.create_button(text='Details')
    roles = QtWidgets.QDialogButtonBox

    box = qtutils.button_box(ok, close, (other, roles.ActionRole))

    assert isinstance(box, QtWidgets.QDialogButtonBox)
    assert box.buttonRole(ok) == roles.AcceptRole
    assert box.buttonRole(close) == roles.RejectRole
    assert box.buttonRole(other) == roles.ActionRole
    assert ok.isDefault()
    assert not close.autoDefault()
    assert not other.autoDefault()
    # macOS dialog buttons are text-only: no icon, no padding space.
    assert ok.icon().isNull()
    assert ok.text() == 'OK'
    assert close.text() == 'Close'


def test_button_box_legacy_keeps_hbox_order(qapp, monkeypatch):
    monkeypatch.setattr(qtutils.defs, 'native_dialog_buttons', False)
    ok = qtutils.ok_button('OK')
    close = qtutils.close_button()
    other = qtutils.create_button(text='Details')
    roles = QtWidgets.QDialogButtonBox

    layout = qtutils.button_box(ok, close, (other, roles.ActionRole))

    assert isinstance(layout, QtWidgets.QHBoxLayout)
    widgets = [
        layout.itemAt(i).widget()
        for i in range(layout.count())
        if layout.itemAt(i).widget() is not None
    ]
    assert widgets == [other, close, ok]
    # Non-macOS keeps the icons and the padding space from create_button().
    assert ok.text() == ' OK'


@pytest.mark.parametrize(
    'text',
    ['Exit Diff Mode', '&Quit Review', 'Setup Remotes', 'Options...', 'Tool About'],
)
def test_add_action_blocks_app_menu_roles_from_labels(qapp, text):
    """Labels Cocoa would merge into About/Preferences/Quit get NoRole"""
    widget = QtWidgets.QWidget()
    action = qtutils.add_action(widget, text, lambda: None)
    assert action.menuRole() == QtWidgets.QAction.NoRole


@pytest.mark.parametrize('text', ['Copy', 'Paste', 'Stage Modified', 'Push...'])
def test_add_action_keeps_the_heuristic_for_other_labels(qapp, text):
    """Cut/Copy/Paste/Select All must still map to the native selectors"""
    widget = QtWidgets.QWidget()
    action = qtutils.add_action(widget, text, lambda: None)
    assert action.menuRole() == QtWidgets.QAction.TextHeuristicRole


def test_create_dock_keeps_floating_docks_visible(qapp):
    """Floating docks must not hide when another app is active on macOS"""
    window = QtWidgets.QMainWindow()
    dock = qtutils.create_dock('Diff', 'Diff', window)
    assert dock.testAttribute(Qt.WA_MacAlwaysShowToolWindow)


def test_native_key_names_on_macos(monkeypatch):
    monkeypatch.setattr(qtutils.core, 'IS_DARWIN', True)
    assert qtutils.native_key_names('Shortcut: Ctrl+Enter') == 'Shortcut: Cmd+Enter'
    assert qtutils.native_key_names('ctrl + 1, alt + a') == 'cmd + 1, option + a'
    assert qtutils.native_key_names('Ctrl + Alt + C') == 'Cmd + Option + C'
    # Only whole words change.
    assert qtutils.native_key_names('Alternatively, Control') == (
        'Alternatively, Control'
    )


def test_native_key_names_elsewhere(monkeypatch):
    monkeypatch.setattr(qtutils.core, 'IS_DARWIN', False)
    assert qtutils.native_key_names('Ctrl + Alt + C') == 'Ctrl + Alt + C'


class _FakeScreen:
    def __init__(self, geometry, available):
        self._geometry = geometry
        self._available = available

    def geometry(self):
        return self._geometry

    def availableGeometry(self):
        return self._available


def _fake_app(monkeypatch, pointer_screen):
    from qtpy import QtCore

    primary = _FakeScreen(
        QtCore.QRect(0, 0, 1920, 1080), QtCore.QRect(0, 25, 1920, 980)
    )

    class App:
        def primaryScreen(self):
            return primary

        def screenAt(self, _pos):
            return pointer_screen

    monkeypatch.setattr(qtutils, 'app', App)
    return primary


def test_desktop_rect_excludes_menu_bar_and_dock_on_macos(monkeypatch):
    from qtpy import QtCore

    monkeypatch.setattr(qtutils.core, 'IS_DARWIN', True)
    _fake_app(monkeypatch, None)
    assert qtutils.desktop_rect() == QtCore.QRect(0, 25, 1920, 980)
    assert qtutils.desktop_size() == (1920, 980)


def test_desktop_rect_uses_the_screen_under_the_pointer_on_macos(monkeypatch):
    from qtpy import QtCore

    monkeypatch.setattr(qtutils.core, 'IS_DARWIN', True)
    other = _FakeScreen(
        QtCore.QRect(1920, 0, 2560, 1440), QtCore.QRect(1920, 0, 2560, 1400)
    )
    _fake_app(monkeypatch, other)
    assert qtutils.desktop_rect() == QtCore.QRect(1920, 0, 2560, 1400)


def test_desktop_rect_unchanged_elsewhere(monkeypatch):
    from qtpy import QtCore

    monkeypatch.setattr(qtutils.core, 'IS_DARWIN', False)
    _fake_app(monkeypatch, None)
    assert qtutils.desktop_rect() == QtCore.QRect(0, 0, 1920, 1080)


def test_dialog_modality_is_a_sheet_on_macos(qapp, monkeypatch):
    monkeypatch.setattr(qtutils.core, 'IS_DARWIN', True)
    parent = QtWidgets.QWidget()
    dialog = QtWidgets.QDialog(parent)
    qtutils.set_dialog_modality(dialog)
    assert dialog.windowModality() == Qt.WindowModal
    orphan = QtWidgets.QDialog()
    qtutils.set_dialog_modality(orphan)
    assert orphan.windowModality() == Qt.ApplicationModal


def test_dialog_modality_unchanged_elsewhere(qapp, monkeypatch):
    monkeypatch.setattr(qtutils.core, 'IS_DARWIN', False)
    parent = QtWidgets.QWidget()
    dialog = QtWidgets.QDialog(parent)
    qtutils.set_dialog_modality(dialog)
    assert dialog.windowModality() == Qt.ApplicationModal


def test_window_menu_has_the_standard_macos_items(qapp):
    window = QtWidgets.QMainWindow()
    menubar = QtWidgets.QMenuBar(window)
    help_menu = menubar.addMenu('Help')
    menu = qtutils.add_window_menu(window, menubar, before=help_menu)
    texts = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert texts == ['Minimize Window', 'Zoom', 'Bring All to Front']
    assert menu.actions()[0].shortcut().toString() == 'Ctrl+M'
    titles = [action.text() for action in menubar.actions()]
    assert titles == ['Window', 'Help']


def test_window_menu_reuses_an_existing_minimize_action(qapp):
    window = QtWidgets.QMainWindow()
    menubar = QtWidgets.QMenuBar(window)
    minimize = qtutils.add_action(window, 'Minimize Window', window.showMinimized)
    menu = qtutils.add_window_menu(window, menubar, minimize_action=minimize)
    assert menu.actions()[0] is minimize


def test_toggle_zoom(qapp):
    window = QtWidgets.QMainWindow()
    window.show()
    qtutils.toggle_zoom(window)
    assert window.isMaximized()
    qtutils.toggle_zoom(window)
    assert not window.isMaximized()


def test_default_monospace_font_on_macos_has_a_bold_face(qapp, monkeypatch):
    monkeypatch.setattr(qtutils.core, 'IS_DARWIN', True)
    monkeypatch.setattr(qtutils.core, 'IS_WIN32', False)
    assert qtutils.default_monospace_font().family() == 'Menlo'


@pytest.mark.parametrize(
    ('text', 'index', 'expect'),
    [
        ('abc', 2, 2),
        ('abc', 10, 3),
        ('abc', 0, 0),
        ('', 5, 0),
        ('café!', 5, 5),  # BMP characters are one UTF-16 unit
        ('a\U0001f600b', 2, 3),  # an emoji is a surrogate pair
        ('a\U0001f600b', 3, 4),
        ('x\ud800y', 3, 3),  # a lone surrogate still counts once
    ],
)
def test_qt_index_from_codepoint(text, index, expect):
    assert qtutils.qt_index_from_codepoint(text, index) == expect


def test_qt_span_from_codepoint_after_an_emoji():
    assert qtutils.qt_span_from_codepoint('\U0001f600 add', 2, 3) == (3, 3)


def test_selected_values_match_a_scan_of_every_child(qapp):
    """Reading the selection model gives what asking each child gave"""
    import random

    tree = QtWidgets.QTreeWidget()
    tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
    sections = []
    for section in range(4):
        top = QtWidgets.QTreeWidgetItem([f'section {section}'])
        tree.addTopLevelItem(top)
        for child in range(30):
            top.addChild(QtWidgets.QTreeWidgetItem([f'{section}/{child}']))
        sections.append(top)
    rng = random.Random(20261029)
    for _ in range(50):
        tree.clearSelection()
        for top in sections:
            for idx in range(top.childCount()):
                if rng.random() < 0.2:
                    top.child(idx).setSelected(True)
            if rng.random() < 0.3:
                top.setSelected(True)  # A selected header is not a child.
        for idx, top in enumerate(sections):
            # The values list can be shorter than the section (a stale model).
            values = [f'value {i}' for i in range(rng.randint(20, 30))]
            assert qtutils.get_selected_values(tree, idx, values) == (
                qtutils.tree_selection(top, values)
            )
            assert qtutils.get_selected_items(tree, idx) == (
                qtutils.tree_selection_items(top)
            )


class _InterruptibleThread(qtutils.Thread):
    """Runs until an interruption is requested"""

    def run(self):
        while not self.isInterruptionRequested():
            self.msleep(5)


def test_thread_halts_when_the_app_quits(qapp):
    """aboutToQuit stops and joins a running qtutils.Thread"""
    thread = _InterruptibleThread()
    thread.start()
    try:
        assert thread.isRunning()
        qapp.aboutToQuit.emit()
        assert thread.isFinished()
    finally:
        thread.halt()


def test_thread_deleted_before_quit_is_disconnected(qapp):
    """A thread deleted with its parent no longer receives aboutToQuit"""
    parent = QtCore.QObject()
    _InterruptibleThread(parent)
    del parent
    # Calling halt() on the deleted thread would raise inside the slot and
    # abort the process.
    qapp.aboutToQuit.emit()


def _direct_qthread_subclasses():
    """Yield (path, class name) for each class in cola/ that subclasses QThread"""
    root = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cola')
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, encoding='utf-8') as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for base in node.bases:
                    if ast.unparse(base) in ('QThread', 'QtCore.QThread'):
                        yield os.path.relpath(path, root), node.name


def test_threads_derive_from_qtutils_thread():
    """A plain QThread still running at teardown aborts the process"""
    allowed = {
        ('qtutils.py', 'Thread'),
        # Stopped and joined by ColaApplication.stop() through Monitor.stop().
        ('fsmonitor.py', '_BaseThread'),
    }
    assert set(_direct_qthread_subclasses()) == allowed


def _raise_value_error():
    raise ValueError('task failed')


def test_failing_task_reports_instead_of_raising(qapp):
    """An exception escaping QRunnable.run() aborts the app through qFatal()"""
    task = qtutils.SimpleTask(_raise_value_error)
    results = []
    finished = []
    task.channel.result.connect(results.append, type=Qt.DirectConnection)
    task.channel.finished.connect(finished.append, type=Qt.DirectConnection)

    task.run()  # Called directly, so no thread pool is involved.

    assert isinstance(task.error, ValueError)
    assert 'ValueError: task failed' in task.error_details
    assert results == []
    assert finished == [task]


def test_run_task_reports_a_failed_task_and_skips_its_callbacks(qapp, monkeypatch):
    critical = MagicMock()
    monkeypatch.setattr(qtutils.Interaction, 'critical', critical)
    runtask = qtutils.RunTask()
    task = qtutils.SimpleTask(_raise_value_error)
    task.run()
    progress = MagicMock()
    finish = MagicMock()
    result = MagicMock()
    runtask.tasks.append(task)
    runtask.task_details[id(task)] = (progress, finish, result)

    runtask.finish(task)

    progress.stop.assert_called_once_with()
    finish.assert_not_called()
    result.assert_not_called()
    critical.assert_called_once()
    assert 'ValueError: task failed' in critical.call_args.kwargs['message']
    assert task not in runtask.tasks


_RAISING_TASK_SCRIPT = """
import sys

from qtpy import QtCore
from qtpy import QtWidgets

from cola import qtutils

app = QtWidgets.QApplication(sys.argv[:1])
runtask = qtutils.RunTask()


def boom():
    raise ValueError('task failed')


runtask.start(qtutils.SimpleTask(boom))
QtCore.QTimer.singleShot(500, app.quit)
app.exec()
QtCore.QThreadPool.globalInstance().waitForDone()
print('survived', flush=True)
"""


def test_a_raising_background_task_does_not_abort_the_app():
    """It aborted with exit code 134 before Task.run() caught exceptions"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=root)
    result = subprocess.run(
        [sys.executable, '-c', _RAISING_TASK_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'survived' in result.stdout
    assert 'ValueError: task failed' in result.stderr
