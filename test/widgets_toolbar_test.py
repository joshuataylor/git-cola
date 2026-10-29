"""Tests for cola.widgets.toolbar"""
import sys
from unittest.mock import MagicMock

import pytest

from cola.widgets import toolbar
from qtpy import QtCore
from qtpy import QtWidgets
from qtpy.QtCore import Qt


@pytest.fixture(scope='module')
def qapp():
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(sys.argv[:1] or ['git-cola-test'])
    yield instance


def _state(x, y):
    return {
        'name': 'Tools',
        'items': [],
        'toolbar_style': toolbar.ToolBar.STYLE_FOLLOW_SYSTEM,
        'visible': True,
        'area': 'top',
        'break': False,
        'float': True,
        'x': x,
        'y': y,
    }


def test_toolbars_stay_visible_when_floating(qapp):
    bar = toolbar.ToolBar.create(MagicMock(), 'Tools')
    assert bar.testAttribute(Qt.WA_MacAlwaysShowToolWindow)


def test_floating_toolbar_on_a_missing_display_is_not_moved_off_screen(qapp):
    window = QtWidgets.QMainWindow()
    state = toolbar.ToolBarState(MagicMock(), window)
    state.apply_state([_state(-50000, -50000)])
    bar = window.findChildren(toolbar.ToolBar)[0]
    assert bar.pos() != QtCore.QPoint(-50000, -50000)


def test_floating_toolbar_on_screen_is_restored_in_place(qapp):
    window = QtWidgets.QMainWindow()
    state = toolbar.ToolBarState(MagicMock(), window)
    state.apply_state([_state(10, 20)])
    bar = window.findChildren(toolbar.ToolBar)[0]
    assert bar.pos() == QtCore.QPoint(10, 20)
