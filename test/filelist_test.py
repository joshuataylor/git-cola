"""Tests for the DAG file list (cola.widgets.filelist)."""
import sys
from unittest.mock import MagicMock

import pytest

from cola.widgets.filelist import FileWidget
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


@pytest.fixture
def file_widget(qapp):
    widget = FileWidget(MagicMock(), None)
    widget.resize(400, 300)
    widget.show()
    qapp.processEvents()
    try:
        yield widget, qapp
    finally:
        widget.close()


def test_filename_column_grows_with_the_widget(file_widget):
    """Until the user intervenes, the Filename column absorbs free space."""
    widget, qapp = file_widget
    initial = widget.columnWidth(0)

    widget.resize(800, 300)
    qapp.processEvents()

    assert widget.columnWidth(0) > initial


def test_manual_column_width_is_kept_across_resizes(file_widget):
    """A user-dragged column width is not snapped back on the next resize."""
    widget, qapp = file_widget

    # Simulate the user dragging the Filename column border.
    widget.header().resizeSection(0, 350)
    qapp.processEvents()
    assert widget._user_adjusted_columns is True

    widget.resize(1000, 300)
    qapp.processEvents()

    assert widget.columnWidth(0) == 350


def test_programmatic_resize_is_not_mistaken_for_a_drag(file_widget):
    """_resize_columns() must not flip the user-adjusted flag on itself."""
    widget, _qapp = file_widget

    widget._resize_columns()

    assert widget._user_adjusted_columns is False


def test_file_list_scrolls_per_pixel(file_widget):
    """The tree inherits smooth per-pixel scrolling from standard.TreeWidget."""
    widget, _qapp = file_widget
    per_pixel = QtWidgets.QAbstractItemView.ScrollPerPixel
    assert widget.verticalScrollMode() == per_pixel
