"""Tests for cola.qtutils helpers."""
import sys

import pytest

from cola import qtutils
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
