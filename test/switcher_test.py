"""Test Quick Switcher"""
import sys
from unittest.mock import MagicMock

import pytest

from cola import icons
from cola.widgets import switcher
from qtpy import QtGui
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


def test_outer_view_is_not_shown_at_construction(qapp):
    """The embedded quick switcher must not appear as a top-level window

    The outer view is placed into its parent's layout, so showing it at
    construction would briefly realise a stray native window at startup.
    """
    dialog = switcher.switcher_outer_view(MagicMock(), QtGui.QStandardItemModel())
    assert not dialog.isVisible()


def test_switcher_item_with_only_key():
    """item text would be key by building item without name"""
    key = 'item-key'
    actual = switcher.switcher_item(key)

    assert actual.key == key
    assert actual.text() == key


def test_switcher_item_with_key_name_icon():
    """item text would be name by building item with key and name"""
    key = 'item-key'
    name = 'item-name'
    icon = icons.folder()

    actual = switcher.switcher_item(key, icon, name)

    assert actual.key == key
    assert actual.text() == name
