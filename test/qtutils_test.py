"""Tests for cola.qtutils helpers."""
import sys

import pytest

from cola import qtutils
from qtpy import QtWidgets


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
