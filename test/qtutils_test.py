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
