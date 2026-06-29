"""Tests for capping the DAG diff size to keep large commits responsive."""
import sys
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from cola.models import prefs
from cola.widgets.diff import CommitDiffWidget
from cola.widgets.diff import _human_size
from cola.widgets.diff import _truncate_diff
from qtpy import QtWidgets

from .helper import app_context

# Prevent unused imports lint errors.
assert app_context is not None


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for widget tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


def test_truncate_diff_unlimited_returns_value():
    """A budget of 0 means unlimited and returns the diff unchanged."""
    diff = 'line\n' * 1000
    assert _truncate_diff(diff, 0) is diff


def test_truncate_diff_under_limit_returns_value():
    """A diff smaller than the byte budget is returned unchanged."""
    diff = 'small diff\n'
    assert _truncate_diff(diff, 1024) is diff


def test_human_size_uses_kb_then_mb():
    """Sizes below 1 MB read as KB; at or above 1 MB they read as MB."""
    assert _human_size(512 * 1024) == '512 KB'
    assert _human_size(1024 * 1024) == '1.0 MB'
    assert _human_size(2 * 1024 * 1024) == '2.0 MB'


def test_truncate_diff_over_limit_is_cut_with_notice():
    """A diff over the budget is cut at a line boundary and gets a notice."""
    budget = 256 * 1024  # 256 KB
    # One megabyte of single-character lines, well over the budget.
    diff = 'x\n' * (1024 * 1024 // 2)

    result = _truncate_diff(diff, budget)

    lines = result.splitlines()
    assert lines[-1].startswith('... diff truncated at 256 KB')
    assert 'total) ...' in lines[-1]
    # The body before the notice never exceeds the requested budget.
    body = result[: result.rfind('\n')]
    assert len(body) <= budget
    # The cut happens on a line boundary -- no partial 'x' line.
    assert body.endswith('x')


def test_truncate_diff_cuts_on_line_boundary():
    """The truncation point is the last newline before the budget."""
    budget = 256 * 1024
    # A short first line, then a line longer than the budget, then more.
    diff = 'a' * (budget // 2) + '\n' + 'b' * budget + '\n' + 'c\n'

    result = _truncate_diff(diff, budget)

    # The 'b' line straddles the budget, so only the first ('a') line survives.
    body = result[: result.rfind('\n')]
    assert set(body) == {'a'}


def _make_widget(app_context):
    widget = CommitDiffWidget(app_context, None, is_commit=True)
    app_context.runtask = MagicMock()
    return widget


def test_start_diff_task_applies_configured_cap(qapp, app_context):
    """The DAG diff view picks up the configured max diff size on each load."""
    widget = _make_widget(app_context)

    with patch('cola.widgets.diff.prefs.dag_max_diff_size', return_value=256):
        widget.set_diff_oid('a' * 40)
        assert widget.diff.max_diff_size == 256
        # The DAG counts in KB.
        assert widget.diff.max_diff_size_unit == 1024

    # Changing the setting takes effect on the next load without a restart.
    with patch('cola.widgets.diff.prefs.dag_max_diff_size', return_value=0):
        widget.set_diff_oid('b' * 40)
        assert widget.diff.max_diff_size == 0


def test_dag_max_diff_size_default():
    """The default cap is 1024 KB (1 MB) and is read from the config key."""
    assert prefs.Defaults.dag_max_diff_size == 1024

    context = MagicMock()
    context.cfg.get.return_value = prefs.Defaults.dag_max_diff_size
    assert prefs.dag_max_diff_size(context) == 1024
    context.cfg.get.assert_called_once_with(
        prefs.DAG_MAX_DIFF_SIZE, default=prefs.Defaults.dag_max_diff_size
    )
