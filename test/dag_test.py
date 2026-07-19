"""Tests DAG functionality"""
import sys
from unittest.mock import patch

import pytest

from cola.models import dag
from cola.widgets.dag import CommitTreeWidget
from cola.widgets.dag import GitDAG
from cola.widgets.dag import _prepare_labels
from cola.widgets.filelist import FileTreeWidgetItem
from cola.widgets.filelist import FileWidget
from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets
from qtpy.QtCore import Qt

from .helper import app_context
from .helper import commit_files

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


LOG_TEXT = """
23e7eab4ba2c94e3155f5d261c693ccac1342eb9^Af4fb8fd5baaa55d9b41faca79be289bb4407281e^A^ADavid Aguilar^AThu Dec 6 18:59:20 2007 -0800^A1196996360^Adavvid@gmail.com^AMerged diffdisplay into main
f4fb8fd5baaa55d9b41faca79be289bb4407281e^Ae3f5a2d0248de6197d6e0e63c901810b8a9af2f8^A^ADavid Aguilar^ATue Dec 4 03:14:56 2007 -0800^A1196766896^Adavvid@gmail.com^ASquashed commit of the following:
e3f5a2d0248de6197d6e0e63c901810b8a9af2f8^Afa5ad6c38be603e2ffd1f9b722a3a5c675f63de2^A^ADavid Aguilar^AMon Dec 3 02:36:06 2007 -0800^A1196678166^Adavvid@gmail.com^AMerged qlistwidgets into main.
103766573cd4e6799d3ee792bcd632b92cf7c6c0^Afa5ad6c38be603e2ffd1f9b722a3a5c675f63de2^A^ADavid Aguilar^ATue Dec 11 05:13:21 2007 -0800^A1197378801^Adavvid@gmail.com^AAdded TODO
fa5ad6c38be603e2ffd1f9b722a3a5c675f63de2^A1ba04ad185cf9f04c56c8482e9a73ef1bd35c695^A^ADavid Aguilar^AFri Nov 30 05:19:05 2007 -0800^A1196428745^Adavvid@gmail.com^AAvoid multiple signoffs
1ba04ad185cf9f04c56c8482e9a73ef1bd35c695^Aad454b189fe5785af397fd6067cf103268b6626e^A^ADavid Aguilar^AFri Nov 30 05:07:47 2007 -0800^A1196428067^Adavvid@gmail.com^Aupdated model/view/controller api
ad454b189fe5785af397fd6067cf103268b6626e^A^A (tag: refs/tags/v0.0)^ADavid Aguilar^AFri Nov 30 00:03:28 2007 -0800^A1196409808^Adavvid@gmail.com^Afirst cut of ugit
""".strip().replace(
    '^A', chr(0x01)
)
LOG_LINES = LOG_TEXT.split('\n')


class DAGTestData:
    """Test data provided by the dag_context fixture"""

    def __init__(self, app_context, head='HEAD', count=1000):
        self.context = app_context
        self.params = dag.DAG(head, count)
        self.reader = dag.RepoReader(app_context, self.params)


@pytest.fixture
def dag_context(app_context):
    """Provide DAGTestData for use by tests"""
    return DAGTestData(app_context)


@patch('cola.models.dag.core')
def test_repo_reader(core, dag_context):
    commit_files()
    dag_context.context.model.update_status()
    expect = len(LOG_LINES)
    actual = 0
    core.run_command.return_value = (0, LOG_TEXT, '')
    for idx, _ in enumerate(dag_context.reader.get()):
        actual += 1

    assert expect == actual


@patch('cola.models.dag.core')
def test_repo_reader_order(core, dag_context):
    commits = [
        'ad454b189fe5785af397fd6067cf103268b6626e',
        '1ba04ad185cf9f04c56c8482e9a73ef1bd35c695',
        'fa5ad6c38be603e2ffd1f9b722a3a5c675f63de2',
        '103766573cd4e6799d3ee792bcd632b92cf7c6c0',
        'e3f5a2d0248de6197d6e0e63c901810b8a9af2f8',
        'f4fb8fd5baaa55d9b41faca79be289bb4407281e',
        '23e7eab4ba2c94e3155f5d261c693ccac1342eb9',
    ]
    core.run_command.return_value = (0, LOG_TEXT, '')
    for idx, commit in enumerate(dag_context.reader.get()):
        assert commits[idx] == commit.oid


@patch('cola.models.dag.core')
def test_repo_reader_parents(core, dag_context):
    parents = [
        [],
        ['ad454b189fe5785af397fd6067cf103268b6626e'],
        ['1ba04ad185cf9f04c56c8482e9a73ef1bd35c695'],
        ['fa5ad6c38be603e2ffd1f9b722a3a5c675f63de2'],
        ['fa5ad6c38be603e2ffd1f9b722a3a5c675f63de2'],
        ['e3f5a2d0248de6197d6e0e63c901810b8a9af2f8'],
        ['f4fb8fd5baaa55d9b41faca79be289bb4407281e'],
    ]
    core.run_command.return_value = (0, LOG_TEXT, '')
    for idx, commit in enumerate(dag_context.reader.get()):
        assert parents[idx] == [p.oid for p in commit.parents]


@patch('cola.models.dag.core')
def test_repo_reader_timestamps(core, dag_context):
    """The raw author timestamp is parsed alongside the formatted date"""
    timestamps = [
        1196409808,
        1196428067,
        1196428745,
        1197378801,
        1196678166,
        1196766896,
        1196996360,
    ]
    core.run_command.return_value = (0, LOG_TEXT, '')
    for idx, commit in enumerate(dag_context.reader.get()):
        assert timestamps[idx] == commit.timestamp
        assert commit.authdate.endswith('2007 -0800')


@patch('cola.models.dag.core')
def test_repo_reader_contract(core, dag_context):
    commit_files()
    dag_context.context.model.update_status()
    core.exists.return_value = True
    core.run_command.return_value = (0, LOG_TEXT, '')

    for idx, _ in enumerate(dag_context.reader.get()):
        pass

    core.run_command.assert_called()
    call_args = core.run_command.call_args

    assert 'log.abbrevCommit=false' in call_args[0][0]
    assert 'log.showSignature=false' in call_args[0][0]


def test_prepare_labels_single_remote_no_condensing():
    refs = ['remotes/origin/main']
    assert _prepare_labels(refs) == [
        ('remotes/origin/main', 'origin/main', None),
    ]


def test_prepare_labels_two_remotes_same_branch():
    refs = ['remotes/origin/main', 'remotes/myremote/main']
    assert _prepare_labels(refs) == [
        ('remotes/myremote/main', 'myremote/main', 'myremote/\u2026'),
        ('remotes/origin/main', 'origin/main', None),
    ]


def test_prepare_labels_three_remotes_same_branch():
    refs = ['remotes/origin/main', 'remotes/open/main', 'remotes/myremote/main']
    assert _prepare_labels(refs) == [
        ('remotes/myremote/main', 'myremote/main', 'myremote/\u2026'),
        ('remotes/open/main', 'open/main', 'open/\u2026'),
        ('remotes/origin/main', 'origin/main', None),
    ]


def test_prepare_labels_mixed_refs():
    refs = [
        'HEAD',
        'remotes/origin/main',
        'remotes/myremote/main',
        'heads/main',
        'tags/v1.0',
    ]
    assert _prepare_labels(refs) == [
        ('tags/v1.0', 'v1.0', None),
        ('remotes/myremote/main', 'myremote/main', 'myremote/\u2026'),
        ('remotes/origin/main', 'origin/main', 'origin/\u2026'),
        ('heads/main', 'main', None),
    ]


def test_prepare_labels_single_remote_with_local():
    refs = ['remotes/origin/main', 'heads/main']
    assert _prepare_labels(refs) == [
        ('remotes/origin/main', 'origin/main', 'origin/\u2026'),
        ('heads/main', 'main', None),
    ]


def test_prepare_labels_different_branch_names_no_condensing():
    refs = ['remotes/origin/main', 'remotes/origin/develop']
    assert _prepare_labels(refs) == [
        ('remotes/origin/develop', 'origin/develop', None),
        ('remotes/origin/main', 'origin/main', None),
    ]


def test_prepare_labels_multiple_groups():
    refs = [
        'remotes/origin/main',
        'remotes/myremote/main',
        'remotes/origin/feat',
        'remotes/myremote/feat',
    ]
    assert _prepare_labels(refs) == [
        ('remotes/myremote/feat', 'myremote/feat', 'myremote/\u2026'),
        ('remotes/origin/feat', 'origin/feat', None),
        ('remotes/myremote/main', 'myremote/main', 'myremote/\u2026'),
        ('remotes/origin/main', 'origin/main', None),
    ]


def test_prepare_labels_empty():
    assert _prepare_labels([]) == []


def test_prepare_labels_no_remotes():
    refs = ['HEAD', 'heads/main', 'tags/v1.0']
    assert _prepare_labels(refs) == [
        ('tags/v1.0', 'v1.0', None),
        ('heads/main', 'main', None),
    ]


def test_prepare_labels_two_groups_with_locals():
    refs = [
        'remotes/origin/main',
        'remotes/myremote/main',
        'heads/main',
        'remotes/origin/feat',
        'heads/feat',
    ]
    assert _prepare_labels(refs) == [
        ('remotes/origin/feat', 'origin/feat', 'origin/\u2026'),
        ('heads/feat', 'feat', None),
        ('remotes/myremote/main', 'myremote/main', 'myremote/\u2026'),
        ('remotes/origin/main', 'origin/main', 'origin/\u2026'),
        ('heads/main', 'main', None),
    ]


def _make_dag_with_lists(app_context):
    """Build a minimal GitDAG wired with real commit and file list widgets.

    Bypasses GitDAG.__init__ (which constructs the full window) but keeps a real
    GitDAG instance so eventFilter()'s super() call resolves correctly.
    """
    win = GitDAG.__new__(GitDAG)
    QtWidgets.QMainWindow.__init__(win)
    win._widgets_initialized = True
    win.maxresults = QtWidgets.QSpinBox()
    win.revtext = QtWidgets.QLineEdit()
    win.treewidget = CommitTreeWidget(app_context, win)
    win.filewidget = FileWidget(app_context, win)
    win.treewidget.installEventFilter(win)
    win.filewidget.installEventFilter(win)
    container = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(container)
    layout.addWidget(win.treewidget)
    layout.addWidget(win.filewidget)
    win.setCentralWidget(container)
    win.show()
    return win


def _tab_event(back=False):
    key = Qt.Key_Backtab if back else Qt.Key_Tab
    return QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, Qt.NoModifier)


def test_tab_cycles_focus_between_commit_and_file_lists(qapp, app_context):
    """Tab and Shift+Tab move focus between the commit list and the file list."""
    win = _make_dag_with_lists(app_context)
    tree = win.treewidget
    files = win.filewidget

    # Spy on each pane's setFocus so the assertions do not depend on the
    # offscreen platform actually delivering and activating focus.
    tree_focus = []
    files_focus = []
    tree.setFocus = lambda *a, **k: tree_focus.append(True)
    files.setFocus = lambda *a, **k: files_focus.append(True)

    # Tab on the commit list -> focus the file list.
    assert win.eventFilter(tree, _tab_event()) is True
    assert len(files_focus) == 1

    # Tab on the file list -> focus the commit list.
    assert win.eventFilter(files, _tab_event()) is True
    assert len(tree_focus) == 1

    # Shift+Tab cycles between the same two panes.
    assert win.eventFilter(tree, _tab_event(back=True)) is True
    assert len(files_focus) == 2


def test_tab_highlights_first_file(qapp, app_context):
    """Tabbing into the file list selects the first file when none is selected."""
    win = _make_dag_with_lists(app_context)
    files = win.filewidget
    for name in ('cola/git.py', 'test/git_test.py'):
        files.addTopLevelItem(FileTreeWidgetItem('6\t1\t' + name))

    assert not files.selectedItems()

    # Tab into the file list.
    assert win.eventFilter(win.treewidget, _tab_event()) is True

    # The first file is both current and selected (highlighted), so arrow keys
    # move from it rather than jumping to the second file.
    current = files.currentItem()
    assert current is not None
    assert current.text(0) == 'cola/git.py'
    assert files.selectedItems() == [current]


def test_tab_keeps_existing_file_selection(qapp, app_context):
    """Tabbing into the file list does not move an existing selection."""
    win = _make_dag_with_lists(app_context)
    files = win.filewidget
    for name in ('cola/git.py', 'test/git_test.py'):
        files.addTopLevelItem(FileTreeWidgetItem('6\t1\t' + name))

    # Pre-select the second file.
    second = files.topLevelItem(1)
    second.setSelected(True)
    files.setCurrentItem(second)

    win.eventFilter(win.treewidget, _tab_event())

    # The existing selection is preserved, not reset to the first file.
    assert files.selectedItems() == [second]
