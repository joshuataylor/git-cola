"""Tests for cola.widgets.standard helpers."""
import os
import subprocess
import sys

import pytest

from cola.widgets import standard
from cola.widgets.standard import _strip_maximized_geometry_flag
from qtpy import QtCore
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


# Real saveGeometry() blob captured from a maximised git-cola main window on
# macOS 15 (recorded from ~/.config/git-cola/settings). The byte at offset 44
# is 0x02 ("maximized" flag). Qt does not always write 0x01 here.
_MAXIMIZED_BLOB_B64 = (
    b'AdnQywADAAAAAAAAAAAAJgAABecAAAPVAAAAAAAAAEIAAAXl'
    + b'AAAD0wAAAAACAAAABegAAAAAAAAAQgAABecAAAPV'
)

# Real saveGeometry() blob captured from a non-maximised window (byte 44 is 0).
_NORMAL_BLOB_B64 = (
    b'AdnQywADAAAAAAHgAAAAGQAAB38AAAQ1AAAB4AAAADUAAAd/'
    + b'AAAENQAAAAAAAAAAB4AAAAHgAAAANQAAB38AAAQ1'
)


def _maximized_byte(blob):
    """Return the byte at the well-known maximized-flag offset."""
    return bytes(blob)[44]


def test_strip_clears_the_maximized_flag():
    """A blob with the maximized flag set comes back with byte 44 zeroed."""
    blob_in = QtCore.QByteArray.fromBase64(_MAXIMIZED_BLOB_B64)
    assert _maximized_byte(blob_in) != 0

    blob_out, was_maximized = _strip_maximized_geometry_flag(blob_in)

    assert was_maximized is True
    assert _maximized_byte(blob_out) == 0


def test_strip_preserves_every_other_byte():
    """Only the maximized-flag byte changes; the rest is byte-identical."""
    blob_in = QtCore.QByteArray.fromBase64(_MAXIMIZED_BLOB_B64)
    blob_out, _ = _strip_maximized_geometry_flag(blob_in)

    raw_in = bytes(blob_in)
    raw_out = bytes(blob_out)
    assert len(raw_in) == len(raw_out)
    assert raw_in[:44] == raw_out[:44]
    assert raw_in[45:] == raw_out[45:]


def test_strip_passes_through_normal_geometry_unchanged():
    """A blob that is not maximized comes back as the same object."""
    blob_in = QtCore.QByteArray.fromBase64(_NORMAL_BLOB_B64)
    assert _maximized_byte(blob_in) == 0

    blob_out, was_maximized = _strip_maximized_geometry_flag(blob_in)

    assert was_maximized is False
    assert blob_out is blob_in


def test_strip_ignores_blobs_without_the_qwidget_geometry_magic():
    """Junk bytes that don't start with the QWidget magic are not modified."""
    junk = QtCore.QByteArray(b'\x00\x00\x00\x00not-a-geometry-blob' + b'\xff' * 60)
    out, was_maximized = _strip_maximized_geometry_flag(junk)
    assert was_maximized is False
    assert out is junk


def test_strip_ignores_truncated_blobs():
    """A blob shorter than the maximized-flag offset is left alone."""
    short = QtCore.QByteArray(b'\x01\xd9\xd0\xcb\x00\x00')
    out, was_maximized = _strip_maximized_geometry_flag(short)
    assert was_maximized is False
    assert out is short


def test_strip_treats_any_non_zero_byte_as_maximized():
    """Qt's saveGeometry() has been observed to write 0x02 at this offset,
    so the helper must accept any non-zero value as 'maximized'."""
    raw = bytearray(bytes(QtCore.QByteArray.fromBase64(_NORMAL_BLOB_B64)))
    raw[44] = 0x02
    blob_in = QtCore.QByteArray(bytes(raw))

    blob_out, was_maximized = _strip_maximized_geometry_flag(blob_in)

    assert was_maximized is True
    assert _maximized_byte(blob_out) == 0


def test_progress_bar_stop_straight_after_start_joins_the_thread(qapp):
    """A stop() that beats the new thread into run() still ends the thread"""
    parent = QtWidgets.QWidget()
    bar = standard.progress_bar(parent)
    thread = bar.progress_thread
    try:
        thread.start()
        thread.stop()
        assert thread.wait(5000)
    finally:
        thread.halt()


def test_progress_bar_halts_its_thread_when_the_app_quits(qapp):
    """aboutToQuit joins the tick thread while its widgets still exist"""
    parent = QtWidgets.QWidget()
    bar = standard.progress_bar(parent)
    bar.start()
    try:
        assert bar.progress_thread.isRunning()
        qapp.aboutToQuit.emit()
        assert bar.progress_thread.isFinished()
    finally:
        bar.progress_thread.halt()
        QtWidgets.QApplication.restoreOverrideCursor()


def test_progress_dialog_halts_its_thread_when_the_app_quits(qapp):
    """aboutToQuit joins the label animation thread"""
    dialog = standard.ProgressDialog('title', 'label', None)
    dialog.start()
    try:
        assert dialog.animation_thread.isRunning()
        qapp.aboutToQuit.emit()
        assert dialog.animation_thread.isFinished()
    finally:
        dialog.stop()


_QUIT_WITH_RUNNING_PROGRESS_BAR = """
import sys

from qtpy import QtCore
from qtpy import QtWidgets

from cola.widgets import standard

app = QtWidgets.QApplication(sys.argv[:1])
window = QtWidgets.QMainWindow()
dock = QtWidgets.QDockWidget()
panel = QtWidgets.QWidget()
bar = standard.progress_bar(panel)
dock.setWidget(panel)
window.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dock)
bar.start()
QtCore.QTimer.singleShot(0, app.quit)
app.exec_()
del window
"""


def test_quitting_with_a_running_progress_bar_does_not_abort():
    """Quitting mid-commit destroyed the commit editor's running tick thread

    Qt aborts the process ("QThread: Destroyed while thread is still
    running"), so this runs in a child process.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=root)
    result = subprocess.run(
        [sys.executable, '-c', _QUIT_WITH_RUNNING_PROGRESS_BAR],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
