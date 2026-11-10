"""Tests for the completion model's background thread"""
import sys
import time

import pytest

from cola.widgets import completion
from qtpy import QtWidgets


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for the thread tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


class _TypingModel:
    """A model whose match text changes on every read, so run() keeps looping"""

    def __init__(self):
        self.reads = 0

    @property
    def match_text(self):
        self.reads += 1
        return str(self.reads)

    def gather_matches(self, _case_sensitive):
        time.sleep(0.005)
        return ((), (), set())


def test_gather_thread_stop_straight_after_start_ends_the_thread(qapp):
    """A stop() that beats the new thread into run() still ends the loop"""
    thread = completion.GatherCompletionsThread(_TypingModel())
    try:
        thread.start()
        thread.stop()
        assert thread.wait(5000)
    finally:
        thread.halt()


def test_gather_thread_halts_when_the_app_quits(qapp):
    """aboutToQuit stops a gather that would otherwise keep re-matching"""
    thread = completion.GatherCompletionsThread(_TypingModel())
    thread.start()
    try:
        assert thread.isRunning()
        qapp.aboutToQuit.emit()
        assert thread.isFinished()
    finally:
        thread.halt()
