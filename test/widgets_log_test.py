"""Tests for the console log (cola.widgets.log)."""
import sys
from unittest.mock import MagicMock

import pytest

from cola.widgets import log
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


def test_log_keeps_only_the_newest_lines(qapp, monkeypatch):
    """Long command output no longer grows the log without limit"""
    monkeypatch.setattr(log.LogWidget, 'MAX_LINES', 50)
    context = MagicMock()
    context.cfg.get = lambda _key, default=None: default
    widget = log.LogWidget(context, display_usage=False)
    widget.append('\n'.join(f'path {idx}' for idx in range(200)))
    document = widget.output_text.document()
    assert document.blockCount() <= 50
    text = document.toPlainText()
    assert 'path 199' in text
    assert 'path 0\n' not in text
