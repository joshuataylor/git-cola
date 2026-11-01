"""Tests DAG functionality"""
import os
import sys
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from cola.models import dag
from cola.widgets import dag as dag_widgets
from cola.widgets.dag import COMMIT_ROLE
from cola.widgets.dag import GRAPH_PREV_ROW_ROLE
from cola.widgets.dag import GRAPH_ROW_ROLE
from cola.widgets.dag import Commit as GraphicsCommit
from cola.widgets.dag import CommitFindWidget
from cola.widgets.dag import CommitTreeWidget
from cola.widgets.dag import CommitTreeWidgetItem
from cola.widgets.dag import FindState
from cola.widgets.dag import GitDAG
from cola.widgets.dag import _prepare_labels
from cola.widgets.dag import _prepare_labels_cached
from cola.widgets.dag import commit_graph
from cola.widgets.dag import commit_matches
from cola.widgets.dag import file_pathspec
from cola.widgets.dag import find_commits_with_git
from cola.widgets.dag import find_matches
from cola.widgets.dag import graph_row_pairs
from cola.widgets.dag import match_spans
from cola.widgets.dag import next_match
from cola.widgets.dag import row_runs
from cola.widgets.dag import sort_by_generation
from cola.widgets.filelist import FileTreeWidgetItem
from cola.widgets.filelist import FileWidget
from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets
from qtpy.QtCore import Qt

from . import helper
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
23e7eab4ba2c94e3155f5d261c693ccac1342eb9^Af4fb8fd5baaa55d9b41faca79be289bb4407281e^A^ADavid Aguilar^AThu Dec 6 18:59:20 2007 -0800^A1196996360^Adavvid@gmail.com^ADavid Aguilar^AThu Dec 6 18:59:20 2007 -0800^A1196996360^Adavvid@gmail.com^AMerged diffdisplay into main
f4fb8fd5baaa55d9b41faca79be289bb4407281e^Ae3f5a2d0248de6197d6e0e63c901810b8a9af2f8^A^ADavid Aguilar^ATue Dec 4 03:14:56 2007 -0800^A1196766896^Adavvid@gmail.com^ADavid Aguilar^ATue Dec 4 03:14:56 2007 -0800^A1196766896^Adavvid@gmail.com^ASquashed commit of the following:
e3f5a2d0248de6197d6e0e63c901810b8a9af2f8^Afa5ad6c38be603e2ffd1f9b722a3a5c675f63de2^A^ADavid Aguilar^AMon Dec 3 02:36:06 2007 -0800^A1196678166^Adavvid@gmail.com^ADavid Aguilar^AMon Dec 3 02:36:06 2007 -0800^A1196678166^Adavvid@gmail.com^AMerged qlistwidgets into main.
103766573cd4e6799d3ee792bcd632b92cf7c6c0^Afa5ad6c38be603e2ffd1f9b722a3a5c675f63de2^A^ADavid Aguilar^ATue Dec 11 05:13:21 2007 -0800^A1197378801^Adavvid@gmail.com^ADavid Aguilar^ATue Dec 11 05:13:21 2007 -0800^A1197378801^Adavvid@gmail.com^AAdded TODO
fa5ad6c38be603e2ffd1f9b722a3a5c675f63de2^A1ba04ad185cf9f04c56c8482e9a73ef1bd35c695^A^ADavid Aguilar^AFri Nov 30 05:19:05 2007 -0800^A1196428745^Adavvid@gmail.com^ADavid Aguilar^AFri Nov 30 05:19:05 2007 -0800^A1196428745^Adavvid@gmail.com^AAvoid multiple signoffs
1ba04ad185cf9f04c56c8482e9a73ef1bd35c695^Aad454b189fe5785af397fd6067cf103268b6626e^A^ADavid Aguilar^AFri Nov 30 05:07:47 2007 -0800^A1196428067^Adavvid@gmail.com^ADavid Aguilar^AFri Nov 30 05:07:47 2007 -0800^A1196428067^Adavvid@gmail.com^Aupdated model/view/controller api
ad454b189fe5785af397fd6067cf103268b6626e^A^A (tag: refs/tags/v0.0)^ADavid Aguilar^AFri Nov 30 00:03:28 2007 -0800^A1196409808^Adavvid@gmail.com^ADavid Aguilar^AFri Nov 30 00:03:28 2007 -0800^A1196409808^Adavvid@gmail.com^Afirst cut of ugit
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


def _mock_core_git_log(core, text, status=0):
    """Configure a mocked cola.models.dag.core to stream `text` like git log.

    RepoReader reads the log a line at a time via start_command/readline/wait,
    so the mock feeds each line back in turn and then an empty string at EOF.
    """
    proc = MagicMock()
    proc.stdout = MagicMock()
    proc.returncode = status
    core.start_command.return_value = proc
    core.readline.side_effect = [line + '\n' for line in text.split('\n')] + ['']
    core.wait.return_value = status


def _read_log_text(core, dag_context):
    """Read LOG_TEXT through the RepoReader, failing if it reads nothing

    The repository needs a commit first: the reader treats a fresh "git init"
    as an empty history, and a loop over nothing would assert nothing.
    """
    commit_files()
    dag_context.context.model.update_status()
    _mock_core_git_log(core, LOG_TEXT)
    commits = list(dag_context.reader.get())
    assert len(commits) == len(LOG_LINES)
    return commits


@patch('cola.models.dag.core')
def test_repo_reader(core, dag_context):
    commit_files()
    dag_context.context.model.update_status()
    expect = len(LOG_LINES)
    actual = 0
    _mock_core_git_log(core, LOG_TEXT)
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
    for idx, commit in enumerate(_read_log_text(core, dag_context)):
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
    for idx, commit in enumerate(_read_log_text(core, dag_context)):
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
    for idx, commit in enumerate(_read_log_text(core, dag_context)):
        assert timestamps[idx] == commit.timestamp
        assert commit.authdate.endswith('2007 -0800')


@patch('cola.models.dag.core')
def test_repo_reader_shares_repeated_strings_and_empty_labels(core, dag_context):
    """Names and emails are stored once, and unlabelled commits allocate nothing"""
    commit_files()
    dag_context.context.model.update_status()
    _mock_core_git_log(core, LOG_TEXT)
    commits = list(dag_context.reader.get())
    assert len(commits) == len(LOG_LINES)
    authors = [commit for commit in commits if commit.author == 'David Aguilar']
    assert len(authors) > 1
    assert all(commit.author is authors[0].author for commit in authors)
    assert all(commit.email is authors[0].email for commit in authors)
    assert all(commit.committer is authors[0].author for commit in authors)

    labelled = [commit for commit in commits if commit.tags]
    unlabelled = [commit for commit in commits if not commit.tags]
    assert labelled and unlabelled
    assert all(commit.tags is unlabelled[0].tags for commit in unlabelled)
    assert all(isinstance(commit.tags, list) for commit in labelled)
    assert labelled[0].tags is not unlabelled[0].tags
    # data() hands out a copy, never the shared or live label list.
    assert labelled[0].data()['tags'] == labelled[0].tags
    assert labelled[0].data()['tags'] is not labelled[0].tags


@patch('cola.models.dag.core')
def test_repo_reader_contract(core, dag_context):
    commit_files()
    dag_context.context.model.update_status()
    core.exists.return_value = True
    _mock_core_git_log(core, LOG_TEXT)

    for idx, _ in enumerate(dag_context.reader.get()):
        pass

    core.start_command.assert_called()
    call_args = core.start_command.call_args

    assert 'log.abbrevCommit=false' in call_args[0][0]
    assert 'log.showSignature=false' in call_args[0][0]


def test_follow_args_single_pathspec():
    """--follow is enabled for exactly one pathspec and nothing else"""
    assert dag._follow_args(['main', '--', 'A']) == ['--follow']
    assert dag._follow_args(['--', 'A']) == ['--follow']
    assert dag._follow_args(['main', '--', 'A', 'B']) == []
    assert dag._follow_args(['main']) == []
    assert dag._follow_args(['main', '--']) == []


@patch('cola.models.dag.core')
def test_repo_reader_follows_single_file(core, app_context):
    """A single-file DAG passes --follow so renames are tracked across history"""
    commit_files()
    app_context.model.update_status()
    _mock_core_git_log(core, LOG_TEXT)

    params = dag.DAG('main -- A', 1000)
    reader = dag.RepoReader(app_context, params)
    list(reader.get())

    assert '--follow' in core.start_command.call_args[0][0]


@patch('cola.models.dag.core')
def test_repo_reader_no_follow_for_multiple_files(core, app_context):
    """--follow is omitted when more than one path is in scope"""
    commit_files()
    app_context.model.update_status()
    _mock_core_git_log(core, LOG_TEXT)

    params = dag.DAG('main -- A B', 1000)
    reader = dag.RepoReader(app_context, params)
    list(reader.get())

    assert '--follow' not in core.start_command.call_args[0][0]


@patch('cola.models.dag.core')
def test_repo_reader_stops_when_interrupted(core, dag_context):
    """A cancelled reader terminates git instead of draining the whole log."""
    commit_files()
    dag_context.context.model.update_status()
    proc = MagicMock()
    proc.stdout = MagicMock()
    proc.returncode = 0
    core.start_command.return_value = proc
    core.readline.side_effect = [line + '\n' for line in LOG_LINES] + ['']

    # Interrupt as soon as the first line is read.
    dag_context.reader._should_interrupt = lambda: True
    commits = list(dag_context.reader.get())

    assert commits == []
    proc.terminate.assert_called_once()


def test_prepare_labels_single_remote_no_condensing():
    refs = ['remotes/origin/main']
    assert list(_prepare_labels(refs)) == [
        ('remotes/origin/main', 'origin/main', None),
    ]


def test_prepare_labels_two_remotes_same_branch():
    refs = ['remotes/origin/main', 'remotes/myremote/main']
    assert list(_prepare_labels(refs)) == [
        ('remotes/myremote/main', 'myremote/main', 'myremote/\u2026'),
        ('remotes/origin/main', 'origin/main', None),
    ]


def test_prepare_labels_three_remotes_same_branch():
    refs = ['remotes/origin/main', 'remotes/open/main', 'remotes/myremote/main']
    assert list(_prepare_labels(refs)) == [
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
    assert list(_prepare_labels(refs)) == [
        ('tags/v1.0', 'v1.0', None),
        ('remotes/myremote/main', 'myremote/main', 'myremote/\u2026'),
        ('remotes/origin/main', 'origin/main', 'origin/\u2026'),
        ('heads/main', 'main', None),
    ]


def test_prepare_labels_single_remote_with_local():
    refs = ['remotes/origin/main', 'heads/main']
    assert list(_prepare_labels(refs)) == [
        ('remotes/origin/main', 'origin/main', 'origin/\u2026'),
        ('heads/main', 'main', None),
    ]


def test_prepare_labels_different_branch_names_no_condensing():
    refs = ['remotes/origin/main', 'remotes/origin/develop']
    assert list(_prepare_labels(refs)) == [
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
    assert list(_prepare_labels(refs)) == [
        ('remotes/myremote/feat', 'myremote/feat', 'myremote/\u2026'),
        ('remotes/origin/feat', 'origin/feat', None),
        ('remotes/myremote/main', 'myremote/main', 'myremote/\u2026'),
        ('remotes/origin/main', 'origin/main', None),
    ]


def test_prepare_labels_empty():
    assert list(_prepare_labels([])) == []


def test_prepare_labels_are_memoised():
    """Repeated calls with the same refs are served from the cache"""
    _prepare_labels_cached.cache_clear()
    refs = ['remotes/origin/main', 'heads/main']
    first = _prepare_labels(refs)
    second = _prepare_labels(list(refs))
    assert first == second
    info = _prepare_labels_cached.cache_info()
    assert info.hits == 1
    assert info.misses == 1


def test_prepare_labels_no_remotes():
    refs = ['HEAD', 'heads/main', 'tags/v1.0']
    assert list(_prepare_labels(refs)) == [
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
    assert list(_prepare_labels(refs)) == [
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


def _commit_for_columns(app_context):
    """Build a commit with every field the column getters read"""
    commit = dag.Commit(app_context, oid='ad454b189fe5785af397fd6067cf103268b6626e')
    commit.summary = 'first cut of ugit'
    commit.author = 'David Aguilar'
    commit.email = 'davvid@gmail.com'
    commit.authdate = 'Fri Nov 30 00:03:28 2007 -0800'
    commit.timestamp = 1196409808
    commit.committer = 'Josh Taylor'
    commit.committer_email = 'joshuataylorx@gmail.com'
    commit.commitdate = 'Fri Nov 30 00:04:00 2007 -0800'
    commit.committer_timestamp = 1196409840
    commit.tags = ['tags/v0.0']
    commit.parsed = True
    return commit


def test_default_columns(qapp, app_context):
    """Only the summary, author and date columns are shown by default"""
    tree = CommitTreeWidget(app_context, None)
    visible = [column.key for _, column in tree.visible_columns()]
    assert visible == ['summary', 'author', 'date']


def test_toggling_a_column_fills_in_its_text(qapp, app_context):
    """Revealing a column populates it for commits that are already displayed"""
    tree = CommitTreeWidget(app_context, None)
    tree.add_commits([_commit_for_columns(app_context)])
    item = tree.topLevelItem(0)

    oid_idx = [idx for idx, column in enumerate(tree.columns) if column.key == 'oid'][0]
    assert tree.isColumnHidden(oid_idx)
    assert item.text(oid_idx) == ''

    tree.set_column_visible(oid_idx, True)

    assert not tree.isColumnHidden(oid_idx)
    assert item.text(oid_idx) == 'ad454b189fe5'
    assert tree.columnWidth(oid_idx) > 0


def test_committer_columns(qapp, app_context):
    """The committer columns read the committer fields, not the author ones"""
    tree = CommitTreeWidget(app_context, None)
    indexes = {column.key: idx for idx, column in enumerate(tree.columns)}
    for key in ('committer', 'committer_email', 'refs'):
        tree.set_column_visible(indexes[key], True)
    tree.add_commits([_commit_for_columns(app_context)])
    item = tree.topLevelItem(0)

    assert item.text(indexes['committer']) == 'Josh Taylor'
    assert item.text(indexes['committer_email']) == 'joshuataylorx@gmail.com'
    assert item.text(indexes['refs']) == 'tags/v0.0'
    assert item.text(indexes['author']) == 'David Aguilar'


def test_required_columns_cannot_be_hidden(qapp, app_context):
    """The summary column holds the inline graph and must stay visible"""
    tree = CommitTreeWidget(app_context, None)
    state = {'column_visibility': {'summary': False, 'author': False}}
    tree.apply_state(state)

    indexes = {column.key: idx for idx, column in enumerate(tree.columns)}
    assert not tree.isColumnHidden(indexes['summary'])
    assert tree.isColumnHidden(indexes['author'])


def test_column_state_round_trip(qapp, app_context):
    """Column visibility and order survive an export/apply cycle"""
    tree = CommitTreeWidget(app_context, None)
    indexes = {column.key: idx for idx, column in enumerate(tree.columns)}
    tree.set_column_visible(indexes['oid'], True)
    tree.set_column_visible(indexes['author'], False)
    tree.header().moveSection(tree.header().visualIndex(indexes['date']), 1)

    state = tree.export_state()

    restored = CommitTreeWidget(app_context, None)
    restored.apply_state(state)

    assert not restored.isColumnHidden(indexes['oid'])
    assert restored.isColumnHidden(indexes['author'])
    assert restored.column_order() == state['column_order']


def test_apply_state_without_column_keys(qapp, app_context):
    """State saved by older versions only carries column widths"""
    tree = CommitTreeWidget(app_context, None)
    tree.apply_state({'column_widths': [100, 200, 300]})

    visible = [column.key for _, column in tree.visible_columns()]
    assert visible == ['summary', 'author', 'date']
    assert tree.columnWidth(0) == 100


def _commit_for_graph(app_context, oid, parents=None):
    """Build a minimal parsed commit for graph/tree tests"""
    commit = dag.Commit(app_context, oid=oid)
    commit.summary = 'summary for ' + oid[:7]
    commit.author = 'David Aguilar'
    commit.authdate = 'Fri Nov 30 00:03:28 2007 -0800'
    commit.timestamp = 1196409808
    commit.parsed = True
    if parents:
        commit.parents = parents
        for parent in parents:
            parent.children.append(commit)
    return commit


def test_add_commits_applies_graph_rows_per_chunk(qapp, app_context):
    """Graph rows attach to each chunk's items and stay batch-local"""
    tree = CommitTreeWidget(app_context, None)
    commit_a = _commit_for_graph(app_context, 'a' * 40)
    commit_b = _commit_for_graph(app_context, 'b' * 40, parents=[commit_a])
    commit_c = _commit_for_graph(app_context, 'c' * 40, parents=[commit_b])
    commit_d = _commit_for_graph(app_context, 'd' * 40, parents=[commit_c])
    # The reader emits oldest-first chunks.
    tree.add_commits([commit_a, commit_b])
    tree.add_commits([commit_c, commit_d])

    items = {}
    order = []
    for idx in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(idx)
        items[item.commit.oid] = item
        order.append(item.commit.oid)
    # Newest-first display order.
    assert order == [commit.oid for commit in (commit_d, commit_c, commit_b, commit_a)]

    summary = CommitTreeWidgetItem.SUMMARY
    for commit in (commit_a, commit_b, commit_c, commit_d):
        item = items[commit.oid]
        row = item.data(summary, GRAPH_ROW_ROLE)
        assert row.commit_oid == commit.oid
        assert item.data(summary, COMMIT_ROLE) is commit
    # The previous row is batch-local: the first row of each chunk has none.
    assert items[commit_b.oid].data(summary, GRAPH_PREV_ROW_ROLE) is None
    assert items[commit_d.oid].data(summary, GRAPH_PREV_ROW_ROLE) is None
    prev_for_a = items[commit_a.oid].data(summary, GRAPH_PREV_ROW_ROLE)
    assert prev_for_a.commit_oid == commit_b.oid
    prev_for_c = items[commit_c.oid].data(summary, GRAPH_PREV_ROW_ROLE)
    assert prev_for_c.commit_oid == commit_d.oid


def test_graph_node_tooltip_is_built_lazily(qapp, app_context):
    """A graph node's tooltip is only built on demand, and only once"""
    commit = _commit_for_graph(app_context, 'a' * 40)
    item = GraphicsCommit(commit)
    assert item.toolTip() == ''

    item.ensure_tooltip()
    tooltip = commit.oid[:12] + ': ' + commit.summary
    assert item.toolTip() == tooltip
    # Idempotent: the second call reuses the built tooltip.
    commit.summary = 'changed'
    item.ensure_tooltip()
    assert item.toolTip() == tooltip


def _paint_centre(item):
    """Paint a graph node into an image and return the colour at its centre"""
    image = QtGui.QImage(40, 40, QtGui.QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QtGui.QPainter(image)
    painter.translate(20, 20)
    option = QtWidgets.QStyleOptionGraphicsItem()
    option.exposedRect = item.boundingRect()
    item.paint(painter, option, None)
    painter.end()
    return image.pixelColor(20, 20).name()


def test_graph_node_colours_follow_selection_and_appearance(qapp, app_context):
    """Selected nodes use the current highlight, even after a Light/Dark switch"""
    commit = _commit_for_graph(app_context, 'a' * 40)
    scene = QtWidgets.QGraphicsScene()
    item = GraphicsCommit(commit)
    scene.addItem(item)
    assert _paint_centre(item) == GraphicsCommit.commit_color.name()

    original = GraphicsCommit.commit_selected_color
    try:
        GraphicsCommit.commit_selected_color = QtGui.QColor('#123456')
        item.setSelected(True)
        assert _paint_centre(item) == '#123456'
        # A later appearance change reaches an already-selected node.
        GraphicsCommit.commit_selected_color = QtGui.QColor('#654321')
        assert _paint_centre(item) == '#654321'
        item.setSelected(False)
        assert _paint_centre(item) == GraphicsCommit.commit_color.name()
    finally:
        GraphicsCommit.commit_selected_color = original


def test_edges_of_one_colour_share_a_pen():
    from cola.widgets.dag import EdgeColor

    EdgeColor.update_colors(dark=False)
    red = QtGui.QColor(Qt.red)
    assert EdgeColor.pen(red) is EdgeColor.pen(QtGui.QColor(Qt.red))
    assert EdgeColor.pen(red) is not EdgeColor.pen(QtGui.QColor(Qt.cyan))
    pen = EdgeColor.pen(red)
    EdgeColor.update_colors(dark=True)
    # New edges after an appearance change never reuse a recoloured pen.
    assert EdgeColor.pen(red) is not pen


def test_model_updated_debounces_display(qapp, app_context):
    """A burst of model updates coalesces into a single display() pass"""
    win = GitDAG.__new__(GitDAG)
    QtWidgets.QMainWindow.__init__(win)
    # Set the mocks before _init_display_timer() so the timeout connects to
    # the mock rather than the real display().
    win.display = MagicMock()
    win.update_window_title = MagicMock()
    win._init_display_timer()
    try:
        win.model_updated()
        win.model_updated()
        win.model_updated()

        # No synchronous display() pass; the burst is pending on the timer.
        assert win.display.call_count == 0
        assert win._display_timer.isActive()
        # The cheap title update still happens immediately.
        assert win.update_window_title.call_count == 3

        win._display_timer.timeout.emit()
        assert win.display.call_count == 1
    finally:
        win._display_timer.stop()
        win.deleteLater()


def _make_lazy_graph_dag(app_context, visible):
    """Build a GitDAG with a mock graphview/dock for lazy-build tests"""
    win = GitDAG.__new__(GitDAG)
    win.graphview = MagicMock()
    win.graphview_dock = MagicMock()
    win.graphview_dock.isVisible.return_value = visible
    win.commit_list = [_commit_for_columns(app_context)]
    win.selection = []
    win.old_selection = []
    win._graph_stale = True
    win._graph_build_id = 1
    win._commits_loaded = False
    # thread_end drives this; it is exercised by its own tests.
    win.restore_selection = MagicMock()
    return win


def test_hidden_graph_dock_defers_build_until_revealed(qapp, app_context):
    """A hidden Graph dock is not populated until it becomes visible."""
    win = _make_lazy_graph_dag(app_context, visible=False)

    win.thread_end()
    # The reader finished, but the hidden canvas was not built.
    assert win._commits_loaded is True
    assert win._graph_stale is True
    win.graphview.add_commits.assert_not_called()

    # Revealing the dock builds the canvas from the full commit list.
    win._graphview_visibility_changed(True)
    win.graphview.add_commits.assert_called_once_with(win.commit_list)
    assert win._graph_stale is False


def test_visible_graph_dock_builds_deferred(qapp, app_context):
    """A visible Graph dock builds on the next event-loop turn, not inline."""
    win = _make_lazy_graph_dag(app_context, visible=True)

    win.thread_end()
    # Deferred: nothing built synchronously inside thread_end.
    win.graphview.add_commits.assert_not_called()

    qapp.processEvents()  # let the singleShot(0) build fire
    win.graphview.add_commits.assert_called_once_with(win.commit_list)
    assert win._graph_stale is False


def test_reveal_before_reader_finishes_does_not_build(qapp, app_context):
    """Revealing the dock mid-load must not lay out a partial history."""
    win = _make_lazy_graph_dag(app_context, visible=True)
    # Reader still running: _commits_loaded is False.
    win._graphview_visibility_changed(True)
    win.graphview.add_commits.assert_not_called()
    assert win._graph_stale is True


@patch('cola.widgets.dag.GitDAG')
def test_git_dag_scopes_ref_to_paths(git_dag_cls, app_context):
    """git_dag(paths=...) builds a '<branch> -- <paths>' path-limited ref"""
    from cola.widgets import dag as dag_widget

    app_context.model.currentbranch = 'main'
    dag_widget.git_dag(app_context, paths=['A', 'B'], show=False)

    params = git_dag_cls.call_args[0][1]
    assert params.ref == 'main -- A B'
    assert params.paths() == ['A', 'B']


@patch('cola.widgets.dag.GitDAG')
def test_git_dag_without_branch_omits_ref(git_dag_cls, app_context):
    """A detached HEAD (no branch) still scopes to the paths via '-- <paths>'"""
    from cola.widgets import dag as dag_widget

    app_context.model.currentbranch = ''
    dag_widget.git_dag(app_context, paths=['A'], show=False)

    params = git_dag_cls.call_args[0][1]
    assert params.ref == '-- A'
    assert params.paths() == ['A']


@patch('cola.widgets.dag.GitDAG')
def test_git_dag_forwards_prefs_model(git_dag_cls, app_context):
    """git_dag() hands the caller's PreferencesModel to the DAG window"""
    from cola.widgets import dag as dag_widget

    prefs_model = MagicMock()
    dag_widget.git_dag(app_context, show=False, prefs_model=prefs_model)

    assert git_dag_cls.call_args[1]['prefs_model'] is prefs_model


def test_preferences_action_has_macos_role_and_shortcut(qapp):
    """The DAG's Preferences action lands in the macOS app menu with Cmd+,"""
    win = GitDAG.__new__(GitDAG)
    QtWidgets.QMainWindow.__init__(win)
    win.context = MagicMock()
    win.prefs_model = MagicMock()
    try:
        action = win._create_preferences_action()
        assert action.menuRole() == QtWidgets.QAction.PreferencesRole
        assert action.shortcut() == QtGui.QKeySequence(QtGui.QKeySequence.Preferences)
    finally:
        win.deleteLater()


def _make_scoped_dag(app_context):
    """A minimal GitDAG wired for the re-scope/navigate_back logic."""
    win = _make_dag_with_lists(app_context)
    win.model = MagicMock()
    win.model.currentbranch = 'main'
    win._scope_history = []
    win.display = MagicMock()
    win.revtext.setText('main')
    return win


def test_navigate_back_steps_through_previous_scopes(qapp, app_context):
    """navigate_back() pops the scope stack one entry at a time."""
    win = _make_scoped_dag(app_context)

    win.histories_selected(['a.txt'])
    assert win.revtext.text() == 'main -- a.txt'
    assert win._scope_history == ['main']

    win.histories_selected(['b.txt'])
    assert win.revtext.text() == 'main -- b.txt'
    assert win._scope_history == ['main', 'main -- a.txt']

    win.navigate_back()
    assert win.revtext.text() == 'main -- a.txt'
    win.navigate_back()
    assert win.revtext.text() == 'main'
    assert win._scope_history == []


def test_navigate_back_with_empty_stack_is_a_no_op(qapp, app_context):
    """Back with nothing to restore leaves the scope unchanged."""
    win = _make_scoped_dag(app_context)

    win.navigate_back()

    assert win.revtext.text() == 'main'
    win.display.assert_not_called()


def test_rescope_to_same_ref_is_not_pushed(qapp, app_context):
    """Re-scoping to the current ref does not stack a duplicate entry."""
    win = _make_scoped_dag(app_context)

    win.histories_selected(['a.txt'])
    win.histories_selected(['a.txt'])

    assert win._scope_history == ['main']


def test_show_entire_history_is_reversible(qapp, app_context):
    """Show-entire-history pushes the prior scope so back restores it."""
    win = _make_scoped_dag(app_context)
    win.histories_selected(['a.txt'])

    win.show_entire_history()
    assert win.revtext.text() == 'main'
    assert win._scope_history == ['main', 'main -- a.txt']

    win.navigate_back()
    assert win.revtext.text() == 'main -- a.txt'


def test_superseded_deferred_build_bails(qapp, app_context):
    """A deferred build from a prior reload no-ops once a new reload starts."""
    win = _make_lazy_graph_dag(app_context, visible=True)
    win._commits_loaded = True

    # A newer reload has bumped the build id since this build was queued.
    win._graph_build_id = 2
    win._populate_graphview(1)

    win.graphview.add_commits.assert_not_called()
    assert win._graph_stale is True


def _hover_tooltip(qapp, tree, item):
    """Deliver a ToolTip help event over item as Qt does and return the text"""
    viewport = tree.viewport()
    pos = tree.visualItemRect(item).center()
    event = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, pos, viewport.mapToGlobal(pos))
    shown = []
    with patch.object(
        dag_widgets.QtWidgets.QToolTip,
        'showText',
        side_effect=lambda _pos, text, *_args: shown.append(text),
    ):
        # QApplication passes the event from the viewport up to the tree,
        # translating the position on the way.
        qapp.sendEvent(viewport, event)
    return shown


def test_message_tooltip_is_for_the_hovered_row(qapp, app_context):
    """Hovering a row shows that row's message, not the next row's

    The tree receives the event in its own coordinates, which sit a header's
    height below the viewport's.
    """
    tree = CommitTreeWidget(app_context, None)
    commits = [_commit_for_graph(app_context, f'{idx:040x}') for idx in range(6)]
    tree.add_commits(commits)
    tree.resize(400, 300)
    tree.show()
    qapp.processEvents()
    assert tree.viewport().y() > 0

    with patch.object(dag_widgets, 'commit_message', side_effect=lambda _ctx, oid: oid):
        for row in range(4):
            item = tree.topLevelItem(row)
            shown = _hover_tooltip(qapp, tree, item)
            assert shown == [dag_widgets.format_message_tooltip(item.commit.oid)]
    tree.close()


def _head_tree(app_context, head_row):
    """A commit list of 6 commits whose row head_row is HEAD"""
    commits = [_commit_for_graph(app_context, f'{idx:040x}') for idx in range(6)]
    # Rows are newest first, so row r shows commits[5 - r].
    commits[5 - head_row].tags = ['HEAD', 'main']
    tree = CommitTreeWidget(app_context, None)
    tree.add_commits(commits)
    return tree


def _press_at(tree):
    event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_At, Qt.ShiftModifier, '@')
    tree.keyPressEvent(event)


def test_at_selects_head(qapp, app_context):
    """ "@" (git's name for HEAD) selects the commit HEAD points at"""
    tree = _head_tree(app_context, head_row=3)
    tree.go_top()
    _press_at(tree)
    head = tree.topLevelItem(3)
    assert tree.selectedItems() == [head]
    assert tree.currentItem() is head


def test_at_leaves_the_selection_without_a_shown_head(qapp, app_context):
    tree = CommitTreeWidget(app_context, None)
    tree.add_commits(
        [_commit_for_graph(app_context, f'{idx:040x}') for idx in range(3)]
    )
    tree.go_top()
    _press_at(tree)
    assert tree.selectedItems() == [tree.topLevelItem(0)]

    # HEAD filtered out by the find bar's Only Matches is not selected either.
    tree = _head_tree(app_context, head_row=2)
    tree.go_top()
    tree.topLevelItem(2).setHidden(True)
    tree.go_head()
    assert tree.selectedItems() == [tree.topLevelItem(0)]


def test_select_moves_the_current_row(qapp, app_context):
    """Selecting from the graph moves the list's current row and anchor"""
    tree = CommitTreeWidget(app_context, None)
    commits = [_commit_for_graph(app_context, f'{idx:040x}') for idx in range(20)]
    tree.add_commits(commits)
    items = [tree.topLevelItem(idx) for idx in range(tree.topLevelItemCount())]
    tree.setCurrentItem(items[3])

    tree.select_commits([items[10].commit])

    assert tree.selectedItems() == [items[10]]
    assert tree.currentItem() is items[10]

    # Down continues from the graph's selection, not the old current row.
    event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier)
    tree.keyPressEvent(event)
    assert tree.currentItem() is items[11]
    assert tree.selectedItems() == [items[11]]


def _graph_rows(tree):
    """(oid, column, previous row's oid) for each row, top to bottom"""
    result = []
    for idx in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(idx)
        row = item.data(CommitTreeWidgetItem.SUMMARY, GRAPH_ROW_ROLE)
        prev = item.data(CommitTreeWidgetItem.SUMMARY, GRAPH_PREV_ROW_ROLE)
        result.append(
            (row.commit_oid, row.commit_column, prev.commit_oid if prev else None)
        )
    return result


def test_graph_built_once_joins_up_across_chunks(qapp, app_context):
    """Rows from one commit_graph() over every commit join up across chunks

    A graph built per 2048-commit chunk started each chunk with no lanes, so
    every lane was cut where two chunks met.
    """
    # Two branches that run side by side across the chunk boundary.
    root = _commit_for_graph(app_context, f'{0:040x}')
    commits = [root]
    left, right = root, root
    for idx in range(1, 9):
        left = _commit_for_graph(app_context, f'{idx:040x}', parents=[left])
        right = _commit_for_graph(app_context, f'{idx + 100:040x}', parents=[right])
        commits.extend([left, right])
    merge = _commit_for_graph(app_context, f'{999:040x}', parents=[left, right])
    commits.append(merge)

    whole = CommitTreeWidget(app_context, None)
    whole.add_commits(commits)

    pairs = {
        pair[0].commit_oid: pair for pair in graph_row_pairs(commit_graph(commits).rows)
    }
    chunked = CommitTreeWidget(app_context, None)
    for start in range(0, len(commits), 6):
        chunk = commits[start : start + 6]
        chunked.add_commits(chunk, graph_rows=[pairs[c.oid] for c in chunk])

    assert _graph_rows(chunked) == _graph_rows(whole)
    # The old way: each chunk on its own breaks the lanes at the boundaries.
    separate = CommitTreeWidget(app_context, None)
    for start in range(0, len(commits), 6):
        separate.add_commits(commits[start : start + 6])
    assert _graph_rows(separate) != _graph_rows(whole)


def test_graph_row_pairs_link_each_row_to_the_one_above():
    rows = ['a', 'b', 'c']
    assert graph_row_pairs(rows) == [('a', None), ('b', 'a'), ('c', 'b')]
    assert graph_row_pairs([]) == []


def test_commit_messages_reads_every_message_in_order(app_context):
    """One git call returns the selected messages in the selection's order"""
    helper.commit_files()
    oids = []
    for name in ('B', 'C'):
        helper.write_file(name, name)
        helper.run_git('add', name)
        helper.run_git(
            'commit', '-q', '-m', f'subject {name}', '-m', 'wrapped\nbody text'
        )
        oids.append(helper.run_git('rev-parse', 'HEAD').strip())
    first, second = oids

    messages = dag_widgets.commit_messages(app_context, [second, dag.WORKTREE, first])
    assert messages == [
        'subject C\n\nwrapped\nbody text',
        'subject B\n\nwrapped\nbody text',
    ]
    unwrapped = dag_widgets.commit_messages(app_context, [first], unwrap=True)
    assert unwrapped == ['subject B\n\nwrapped body text']
    assert unwrapped == [dag_widgets.commit_message(app_context, first, unwrap=True)]
    assert dag_widgets.commit_messages(app_context, [dag.STAGE]) == []
    # A bad oid fails the batch: fall back to one call each.
    missing = '0' * 40
    assert dag_widgets.commit_messages(app_context, [first, missing]) == [
        'subject B\n\nwrapped\nbody text',
        '',
    ]


def _reader_dag(app_context):
    """A GitDAG stand-in with just what the reader thread plumbing uses"""
    win = GitDAG.__new__(GitDAG)
    QtWidgets.QMainWindow.__init__(win)
    win.context = app_context
    win.params = dag.DAG('HEAD', 10)
    win._stopping_threads = set()
    win.thread_begin = MagicMock()
    win.thread_status = MagicMock()
    win.add_commits = MagicMock()
    win.thread_end = MagicMock()
    return win


def test_a_reader_still_stopping_is_replaced_not_reused(qapp, app_context):
    """start() on a running QThread does nothing, which dropped the new query"""
    win = _reader_dag(app_context)
    fresh = MagicMock()
    win._new_reader_thread = lambda: fresh  # no real thread in the suite
    old = MagicMock()
    old.isRunning.return_value = True
    win.thread = old
    win._start_reader_thread()
    assert win.thread is fresh
    fresh.start.assert_called_once()
    assert old in win._stopping_threads
    old.start.assert_not_called()

    idle = MagicMock()
    idle.isRunning.return_value = False
    win.thread = idle
    win._start_reader_thread()
    assert win.thread is idle
    idle.start.assert_called_once()


def test_a_replaced_reader_signals_are_ignored(qapp, app_context):
    win = _reader_dag(app_context)
    old = win._new_reader_thread()
    win.thread = old
    old.add.emit(['first'])
    qapp.processEvents()
    win.add_commits.assert_called_once_with(['first'])

    win.thread = win._new_reader_thread()
    old.add.emit(['stale'])
    old.end.emit()
    qapp.processEvents()
    win.add_commits.assert_called_once_with(['first'])
    win.thread_end.assert_not_called()


def test_a_second_reader_does_not_disturb_one_in_progress(app_context):
    """Each RepoReader has its own CommitFactory

    The commit message menu and the sequence editor read commits too. With
    one class-level factory, their reset cleared it under the DAG's reader
    mid-parse, so its commits got parents that weren't in its own list.
    """
    helper.commit_files()
    for name in ('B', 'C', 'D', 'E'):
        helper.write_file(name, name)
        helper.run_git('add', name)
        helper.run_git('commit', '-q', '-m', name)
    app_context.model.update_status()  # the reader checks for local branches

    first = dag.RepoReader(app_context, dag.DAG('HEAD', 10))
    reading = first.get()
    commits = [next(reading), next(reading)]
    other = dag.RepoReader(app_context, dag.DAG('HEAD', 2))
    assert len(list(other.get())) == 2
    commits.extend(reading)

    assert len(commits) == 5
    by_oid = {commit.oid: commit for commit in commits}
    for commit in commits:
        for parent in commit.parents:
            assert by_oid[parent.oid] is parent
            assert commit in parent.children


def test_set_labels_replaces_tags_and_branches(app_context):
    commit = _commit_for_columns(app_context)
    commit.set_labels(' (HEAD -> refs/heads/main, tag: refs/tags/v1, refs/remotes/o/x)')
    assert list(commit.tags) == ['HEAD', 'heads/main', 'tags/v1', 'remotes/o/x']
    assert list(commit.branches) == ['main']
    commit.set_labels('')
    assert list(commit.tags) == []
    assert list(commit.branches) == []


def test_relabel_commits_moves_labels_without_a_reload(qapp, app_context):
    """A branch created, moved or deleted relabels only the commits involved"""
    helper.commit_files()
    for name in ('B', 'C'):
        helper.write_file(name, name)
        helper.run_git('add', name)
        helper.run_git('commit', '-q', '-m', name)
    app_context.model.update_status()  # the reader checks for local branches
    reader = dag.RepoReader(app_context, dag.DAG('HEAD', 10))
    reader.reset()
    commits = list(reader.get())
    first, _middle, head = commits

    win = GitDAG.__new__(GitDAG)
    QtWidgets.QMainWindow.__init__(win)
    win.context = app_context
    win.model = app_context.model
    win.commits = {}
    for commit in commits:
        win.commits[commit.oid] = commit
        for tag in commit.tags:
            win.commits[tag] = commit
    win.treewidget = CommitTreeWidget(app_context, win)
    win.treewidget.add_commits(commits)
    win.graphview = MagicMock()
    win.graphview_dock = MagicMock()
    win.graphview_dock.isVisible.return_value = False
    win._graph_stale = False
    win._graph_build_id = 1
    tree = win.treewidget

    before = win._ref_state()
    helper.run_git('branch', 'topic', first.oid)
    after = win._ref_state()
    win.relabel_commits(before, after)
    assert 'heads/topic' in first.tags
    assert win.commits['heads/topic'] is first
    assert tree.oidmap['heads/topic'] is tree.oidmap[first.oid]
    # Labels lay out the graph canvas, so it is rebuilt on its next showing.
    win.graphview.clear.assert_called_once()
    assert win._graph_stale

    helper.run_git('branch', '-f', 'topic', head.oid)
    moved = win._ref_state()
    win.relabel_commits(after, moved)
    assert 'heads/topic' not in first.tags
    assert 'heads/topic' in head.tags
    assert 'HEAD' in head.tags
    assert tree.oidmap['heads/topic'] is tree.oidmap[head.oid]

    helper.run_git('branch', '-D', 'topic')
    win.relabel_commits(moved, win._ref_state())
    assert 'heads/topic' not in head.tags
    assert 'heads/topic' not in win.commits
    assert 'heads/topic' not in tree.oidmap


def test_row_runs_groups_consecutive_rows():
    assert list(row_runs([])) == []
    assert list(row_runs([4])) == [(4, 4)]
    assert list(row_runs([0, 1, 2, 5, 7, 8])) == [(0, 2), (5, 5), (7, 8)]


def _chunked_tree(app_context, count=30, chunk=10):
    """A commit list loaded in oldest-first chunks, as the reader delivers it"""
    tree = CommitTreeWidget(app_context, None)
    commits = [_commit_for_graph(app_context, f'{idx:040x}') for idx in range(count)]
    for start in range(0, count, chunk):
        tree.add_commits(commits[start : start + chunk])
    items = [tree.topLevelItem(idx) for idx in range(tree.topLevelItemCount())]
    return tree, items


def test_select_applies_runs_of_rows_as_ranges(qapp, app_context):
    """Selecting many commits builds one range per run of rows

    One setSelected() per row merged each into the selection model, which
    took 389 s for Select All on 50000 commits.
    """
    tree, items = _chunked_tree(app_context)
    # Rows are newest first across all three chunks.
    assert [item.commit.oid for item in items] == [
        f'{idx:040x}' for idx in reversed(range(30))
    ]
    wanted = [items[row] for row in (2, 3, 4, 15, 16, 28)]

    tree.select([item.commit.oid for item in wanted])

    assert tree.selectedItems() == wanted
    assert tree.selectionModel().selection().count() == 3
    assert tree.currentItem() is wanted[0]

    tree.select([item.commit.oid for item in items])
    assert len(tree.selectedItems()) == 30
    assert tree.selectionModel().selection().count() == 1

    tree.select([])
    assert tree.selectedItems() == []


def test_select_commits_skips_the_echo_of_its_own_selection(qapp, app_context):
    """The list's own selection coming back from GitDAG is not reapplied"""
    tree, items = _chunked_tree(app_context, count=6, chunk=6)
    for item in items[1:4]:
        item.setSelected(True)
    tree.selection_changed()
    emitted = sort_by_generation([item.commit for item in items[1:4]])
    calls = []
    tree.select = calls.append

    tree.select_commits(emitted)
    assert calls == []

    tree.select_commits([items[5].commit])
    assert calls == [[items[5].commit.oid]]


def test_commit_matches_fields(app_context):
    """Find matches the summary, author, email, refs and an oid prefix"""
    commit = _commit_for_columns(app_context)
    assert commit_matches(commit, 'cut of')
    assert commit_matches(commit, 'aguilar')
    assert commit_matches(commit, 'davvid@')
    assert commit_matches(commit, 'v0.0')
    assert commit_matches(commit, 'ad454b')
    assert commit_matches(commit, 'AD454B')
    # The oid only matches as a prefix, so short hex strings stay meaningful.
    assert not commit_matches(commit, '5785af')
    # The committer is a find field too.
    assert commit_matches(commit, 'josh taylor')
    assert commit_matches(commit, 'joshuataylorx@')
    assert not commit_matches(commit, 'no such person')
    assert not commit_matches(commit, '')
    # Fields are searched separately, so a match can't span two of them.
    assert not commit_matches(commit, 'aguilardavvid')


def test_commit_matches_case_sensitive(app_context):
    """Match Case compares the text exactly"""
    commit = _commit_for_columns(app_context)
    assert commit_matches(commit, 'Aguilar', case_sensitive=True)
    assert not commit_matches(commit, 'aguilar', case_sensitive=True)


def test_find_matches_returns_rows_in_order(app_context):
    """find_matches returns the indices of the matching commits"""
    commits = [_commit_for_graph(app_context, f'{idx:040x}') for idx in range(5)]
    commits[1].summary = 'fix the parser'
    commits[3].summary = 'Fix the lexer'
    assert find_matches(commits, 'fix') == [1, 3]
    assert find_matches(commits, 'Fix', case_sensitive=True) == [3]
    assert find_matches(commits, 'nothing') == []


def test_next_match_steps_and_wraps():
    """next_match moves forwards and backwards and wraps around the ends"""
    matches = [2, 5, 9]
    assert next_match(matches, -1) == 2
    assert next_match(matches, 2) == 5
    assert next_match(matches, 9) == 2
    assert next_match(matches, 2, backwards=True) == 9
    assert next_match(matches, 5, backwards=True) == 2
    assert next_match(matches, 7, backwards=True) == 5
    assert next_match(matches, 5, inclusive=True) == 5
    assert next_match(matches, 6, inclusive=True) == 9
    assert next_match(matches, 5, backwards=True, inclusive=True) == 5
    assert next_match([], 0) is None


def _find_tree(app_context, git=False):
    """Build a commit list whose rows 1, 3 and 4 match "fix"

    The git searches (full messages, changed files) are off unless git=True,
    so the in-memory matching can be checked on its own.
    """
    tree = CommitTreeWidget(app_context, None)
    commits = [_commit_for_graph(app_context, f'{idx:040x}') for idx in range(6)]
    tree.add_commits(commits)
    items = [tree.topLevelItem(idx) for idx in range(tree.topLevelItemCount())]
    for row in (1, 3, 4):
        items[row].commit.summary = f'fix number {row}'
    tree.commits_changed.emit()
    find = CommitFindWidget(tree, None)
    find.messages_checkbox.setChecked(git)
    find.files_checkbox.setChecked(git)
    return tree, find, items


class _SyncRunTask:
    """Run a find bar task at once, so no thread outlives the test"""

    def start(self, task, result=None, **_kwargs):
        result(task.task())


def _git_find_tree(app_context, monkeypatch, found):
    """A find bar whose git searches return found[kind][needle]"""
    tree, find, items = _find_tree(app_context, git=True)
    calls = []
    searches = []

    class FakeSearch:
        def __init__(self, context, oids, needle, kind, case_sensitive=False):
            self.args = (needle, kind, case_sensitive, len(oids))
            self.cancelled = False
            searches.append(self)

        def run(self):
            calls.append(self.args)
            needle, kind = self.args[:2]
            return set(found.get(kind, {}).get(needle, ()))

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(dag_widgets, 'GitFindSearch', FakeSearch)
    find._runtask = _SyncRunTask()
    find._searches_made = searches
    # Only the tests' own timeout.emit() starts a search: the real interval
    # can expire inside processEvents() and make the test depend on timing.
    find._git_timer.setInterval(3600 * 1000)
    return tree, find, items, calls


def test_file_pathspec_matches_a_substring_of_any_path():
    assert file_pathspec('cargo') == ':(icase,glob)**/*cargo*'
    assert file_pathspec('Cargo', case_sensitive=True) == ':(glob)**/*Cargo*'
    # Glob characters in the text are matched literally.
    assert file_pathspec('a*b?[c]\\') == ':(icase,glob)**/*a\\*b\\?\\[c\\]\\\\*'


def test_find_commits_with_git_searches_messages_and_files(app_context):
    """git reads only the given commits, by full message or changed path"""
    os.makedirs('docs')
    os.makedirs('src')
    helper.write_file('docs/Guide.txt', 'one\n')
    helper.run_git('add', 'docs/Guide.txt')
    helper.run_git('commit', '-q', '-m', 'add docs\n\nThe body mentions Widgets.')
    guide = helper.run_git('rev-parse', 'HEAD').strip()
    helper.write_file('src/main.c', 'two\n')
    helper.run_git('add', 'src/main.c')
    helper.run_git('commit', '-q', '-m', 'add code')
    code = helper.run_git('rev-parse', 'HEAD').strip()
    oids = [guide, code]

    search = find_commits_with_git
    assert search(app_context, oids, 'widgets', 'message') == {guide}
    assert search(app_context, oids, 'widgets', 'message', True) == set()
    assert search(app_context, oids, 'Widgets', 'message', True) == {guide}
    assert search(app_context, oids, 'guide', 'files') == {guide}
    assert search(app_context, oids, 'guide', 'files', True) == set()
    assert search(app_context, oids, 'main.c', 'files') == {code}
    assert search(app_context, oids, 'src/m', 'files') == {code}
    # Only the commits passed in are searched.
    assert search(app_context, [code], 'widgets', 'message') == set()
    assert search(app_context, [], 'widgets', 'message') == set()


def test_commit_find_selects_as_you_type(qapp, app_context):
    """Typing selects the first matching commit and shows the match count"""
    tree, find, items = _find_tree(app_context)
    find.text.setText('fix')
    assert tree.selectedItems() == [items[1]]
    assert tree.currentItem() is items[1]
    assert find.count_label.text() == '1/3'

    # Narrowing the text keeps the current commit while it still matches.
    find.text.setText('fix number 3')
    assert tree.selectedItems() == [items[3]]
    assert find.count_label.text() == '1/1'


def test_commit_find_next_and_previous(qapp, app_context):
    """Next and previous step through the matches and wrap around"""
    tree, find, items = _find_tree(app_context)
    find.text.setText('fix')
    find.search()
    assert tree.selectedItems() == [items[3]]
    assert find.count_label.text() == '2/3'
    find.search()
    find.search()
    assert tree.selectedItems() == [items[1]]
    find.search_backwards()
    assert tree.selectedItems() == [items[4]]
    assert find.count_label.text() == '3/3'


def test_commit_find_no_matches(qapp, app_context):
    """Text that matches nothing leaves the selection alone"""
    tree, find, items = _find_tree(app_context)
    tree.select([items[2].commit.oid])
    find.text.setText('no such commit')
    assert tree.selectedItems() == [items[2]]
    assert find.count_label.text() == ''


def test_commit_find_rescans_added_commits(qapp, app_context):
    """Commits added after a search are found by the next one"""
    tree, find, items = _find_tree(app_context)
    find.text.setText('fix')
    newer = _commit_for_graph(app_context, f'{99:040x}')
    newer.summary = 'fix in a newer commit'
    tree.add_commits([newer])
    tree.setCurrentItem(None)
    find.search()
    assert tree.selectedItems()[0].commit is newer
    assert find.count_label.text() == '1/4'


def test_commit_find_adds_git_matches_after_typing_pauses(
    qapp, app_context, monkeypatch
):
    """Full-message and changed-file matches join the in-memory ones"""
    found = {'message': {}, 'files': {}}
    tree, find, items, calls = _git_find_tree(app_context, monkeypatch, found)
    found['message']['fix'] = {items[2].commit.oid}
    found['files']['fix'] = {items[5].commit.oid}

    find.text.setText('fix')
    # The summaries match at once, and git runs once typing pauses.
    assert tree.selectedItems() == [items[1]]
    assert find.count_label.text() == '1/3 searching...'
    assert calls == []
    assert find._git_timer.isActive()

    find._git_timer.timeout.emit()
    assert sorted(call[1] for call in calls) == ['files', 'message']
    assert find.count_label.text() == '1/5'
    # The selection stays on the in-memory match, and Next reaches git's.
    find.search()
    assert tree.selectedItems() == [items[2]]
    find.search()
    find.search()
    find.search()
    assert tree.selectedItems() == [items[5]]
    assert find.count_label.text() == '5/5'


def test_commit_find_jumps_to_a_git_match_when_nothing_else_matches(
    qapp, app_context, monkeypatch
):
    found = {'message': {'body text': set()}, 'files': {'body text': set()}}
    tree, find, items, _calls = _git_find_tree(app_context, monkeypatch, found)
    found['message']['body text'] = {items[4].commit.oid}
    tree.select([items[0].commit.oid])

    find.text.setText('body text')
    assert tree.selectedItems() == [items[0]]
    assert find.count_label.text() == 'searching...'
    find._git_timer.timeout.emit()
    assert tree.selectedItems() == [items[4]]
    assert find.count_label.text() == '1/1'


def test_commit_find_moves_to_an_earlier_git_match_unless_the_user_moved(
    qapp, app_context, monkeypatch
):
    """A git match above the one find-as-you-type picked takes over"""
    found = {'message': {'fix': set()}, 'files': {'fix': set()}}
    tree, find, items, _calls = _git_find_tree(app_context, monkeypatch, found)
    found['files']['fix'] = {items[0].commit.oid}
    tree.setCurrentItem(None)

    find.text.setText('fix')
    assert tree.selectedItems() == [items[1]]
    find._git_timer.timeout.emit()
    assert tree.selectedItems() == [items[0]]
    assert find.count_label.text() == '1/4'

    # Once the user has moved to another match, a late batch leaves it alone.
    find.search()
    find.search()
    assert tree.selectedItems() == [items[3]]
    find._apply_git_matches(find._generation, {items[2].commit.oid})
    assert tree.selectedItems() == [items[3]]
    assert find.count_label.text() == '4/5'


def test_commit_find_drops_git_results_for_old_text(qapp, app_context, monkeypatch):
    """A result that arrives after the text changed is ignored"""
    tree, find, items, _calls = _git_find_tree(app_context, monkeypatch, {})
    find.text.setText('first')
    stale = find._generation
    find.text.setText('second')
    find._apply_git_matches(stale, {items[2].commit.oid})
    assert find._git_oids == set()
    assert items[2] not in tree.selectedItems()


def test_commit_find_skips_git_for_short_text_and_unchecked_boxes(
    qapp, app_context, monkeypatch
):
    tree, find, items, calls = _git_find_tree(app_context, monkeypatch, {})
    # "x" only appears in the "fix number" summaries.
    find.text.setText('x')
    assert not find._git_timer.isActive()
    assert find.count_label.text() == '1/3'

    find.text.setText('fix')
    find.files_checkbox.click()
    qapp.processEvents()  # checkbox clicks are queued
    find._git_timer.timeout.emit()
    assert [kind for _needle, kind, _case, _count in calls] == ['message']

    find.messages_checkbox.click()
    qapp.processEvents()  # checkbox clicks are queued
    assert not find._git_timer.isActive()
    assert find.count_label.text() == '1/3'


def test_commit_find_stops_searches_it_no_longer_needs(qapp, app_context, monkeypatch):
    """Changing the text, reloading or closing stops the running git searches"""
    tree, find, _items, _calls = _git_find_tree(app_context, monkeypatch, {})
    find.text.setText('first')
    find._runtask = MagicMock()  # leave the searches "running"
    find._git_timer.timeout.emit()
    first = list(find._searches_made)
    assert len(first) == 2
    assert not any(search.cancelled for search in first)

    find.text.setText('second')
    assert all(search.cancelled for search in first)
    find._git_timer.timeout.emit()
    second = find._searches_made[2:]
    find.show()
    tree.commits_changed.emit()
    assert all(search.cancelled for search in second)
    find._git_timer.timeout.emit()
    third = find._searches_made[4:]
    find.hide_find()
    assert all(search.cancelled for search in third)


def test_git_find_search_cancel_stops_its_process(monkeypatch):
    """cancel() before the start skips git, and after it kills the process"""
    started = []

    class FakeProc:
        returncode = None

        def __init__(self):
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def communicate(self, stdin):
            search.cancel()  # the find bar stops it while git runs
            self.returncode = -15
            return b'', b''

    def start_command(cmd, **kwargs):
        started.append(cmd)
        return proc

    monkeypatch.setattr(dag_widgets.core, 'start_command', start_command)
    search = dag_widgets.GitFindSearch(None, ['a' * 40], 'text', 'message')
    search.cancel()
    assert search.run() == set()
    assert started == []

    proc = FakeProc()
    search = dag_widgets.GitFindSearch(None, ['a' * 40], 'text', 'files')
    assert search.run() == set()
    assert proc.terminated
    assert started[0][:2] == ['git', 'log']


def test_match_spans_finds_each_occurrence():
    assert match_spans('Fix the fix', 'fix') == [(0, 3), (8, 3)]
    assert match_spans('Fix the fix', 'fix', case_sensitive=True) == [(8, 3)]
    assert match_spans('aaaa', 'aa') == [(0, 2), (2, 2)]
    assert match_spans('', 'x') == []
    assert match_spans('text', '') == []


def test_find_state_badge_names_matches_that_are_not_on_screen(app_context):
    commit = _commit_for_columns(app_context)
    visible = {'summary', 'author', 'date'}
    # Matches in visible cells are highlighted, so they need no badge.
    assert FindState('ugit').badge(commit, visible) == ''
    assert FindState('aguilar').badge(commit, visible) == ''
    assert FindState('v0.0').badge(commit, visible) == 'in ref'
    assert FindState('v0.0').badge(commit, visible | {'refs'}) == ''
    assert FindState('ad454b').badge(commit, visible) == 'in hash'
    assert FindState('josh').badge(commit, visible) == 'in committer, committer email'
    git_kinds = {commit.oid: {'message', 'files'}}
    assert FindState('body', git_kinds=git_kinds).badge(commit, visible) == (
        'in message, files'
    )
    # The summary starts the message, so a summary match doesn't name it.
    assert FindState('ugit', git_kinds=git_kinds).badge(commit, visible) == ('in files')


def _hidden_rows(tree):
    root = QtCore.QModelIndex()
    return [
        row for row in range(tree.topLevelItemCount()) if tree.isRowHidden(row, root)
    ]


def test_commit_find_shows_only_the_matching_commits(qapp, app_context):
    """With Only Matches on, the list hides every commit that doesn't match"""
    tree, find, items = _find_tree(app_context)
    find.show()
    find.text.setText('fix')
    assert _hidden_rows(tree) == [0, 2, 5]
    assert tree.find_state.needle == 'fix'
    assert tree.find_state.filtering

    # Commits added while filtering keep the right rows hidden.
    newer = _commit_for_graph(app_context, f'{99:040x}')
    newer.summary = 'fix in a newer commit'
    older_hidden = [items[0], items[2], items[5]]
    tree.add_commits([newer])
    assert [item.isHidden() for item in older_hidden] == [True, True, True]
    assert not tree.topLevelItem(0).isHidden()

    find.only_matches_checkbox.click()
    qapp.processEvents()  # checkbox clicks are queued
    assert _hidden_rows(tree) == []
    assert not tree.find_state.filtering

    find.only_matches_checkbox.click()
    qapp.processEvents()
    assert _hidden_rows(tree) != []
    find.text.setText('')
    assert _hidden_rows(tree) == []
    assert tree.find_state is None


def test_commit_find_closing_shows_every_commit(qapp, app_context):
    tree, find, _items = _find_tree(app_context)
    find.show()
    find.text.setText('fix number 3')
    assert len(_hidden_rows(tree)) == 5
    find.hide_find()
    assert _hidden_rows(tree) == []
    assert tree.find_state is None


def _highlighted_pixels(image):
    """Count the pixels tinted by FindState.highlight over a light background"""
    count = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            if color.red() > 200 and color.green() > 180 and color.blue() < 170:
                count += 1
    return count


def test_commit_find_highlights_the_matched_text(qapp, app_context):
    """The matches are marked in the painted list, with and without the graph"""
    tree, find, _items = _find_tree(app_context)
    tree.resize(600, 220)
    tree.setColumnWidth(CommitTreeWidgetItem.SUMMARY, 300)
    palette = tree.palette()
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor(Qt.white))
    palette.setColor(QtGui.QPalette.Text, QtGui.QColor(Qt.black))
    tree.setPalette(palette)
    tree.clearSelection()
    assert _highlighted_pixels(tree.grab().toImage()) == 0

    tree.set_find_state(FindState('summary'))
    assert _highlighted_pixels(tree.grab().toImage()) > 0
    tree.display_inline_graph(False)
    assert _highlighted_pixels(tree.grab().toImage()) > 0
    tree.set_find_state(None)
    assert _highlighted_pixels(tree.grab().toImage()) == 0


def _dark_pixels(image, rect):
    """Count the near-black pixels inside rect"""
    count = 0
    for y in range(rect.top(), rect.bottom() + 1):
        for x in range(rect.left(), rect.right() + 1):
            color = image.pixelColor(x, y)
            if color.red() < 60 and color.green() < 60 and color.blue() < 60:
                count += 1
    return count


def test_selected_row_text_uses_the_highlighted_text_colour(qapp, app_context):
    """With the inline graph, a selected row's summary and badge are drawn light

    Black text stayed black on the selection colour while the other columns
    turned white.
    """
    tree = CommitTreeWidget(app_context, None)
    commits = [_commit_for_graph(app_context, f'{idx:040x}') for idx in range(3)]
    tree.add_commits(commits)
    tree.display_inline_graph(True)
    tree.resize(600, 200)
    tree.setColumnWidth(CommitTreeWidgetItem.SUMMARY, 400)
    palette = tree.palette()
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor(Qt.white))
    palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(Qt.white))
    palette.setColor(QtGui.QPalette.Text, QtGui.QColor(Qt.black))
    palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(0, 70, 200))
    palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(Qt.white))
    tree.setPalette(palette)
    selected = tree.topLevelItem(0)
    plain = tree.topLevelItem(1)
    tree.setCurrentItem(selected)
    # The badge names a match only git found, so every row shows it.
    git_kinds = {commit.oid: {'message'} for commit in commits}
    tree.set_find_state(FindState('needle', git_kinds=git_kinds))

    image = tree.viewport().grab().toImage()
    summary_left = tree.header().sectionPosition(CommitTreeWidgetItem.SUMMARY)
    summary_width = tree.header().sectionSize(CommitTreeWidgetItem.SUMMARY)

    def cell(item):
        rect = tree.visualItemRect(item)
        return QtCore.QRect(summary_left, rect.top(), summary_width, rect.height())

    assert _dark_pixels(image, cell(plain)) > 0
    assert _dark_pixels(image, cell(selected)) == 0


class _MarkView:
    def __init__(self):
        self.calls = []

    def set_find_highlight(self, needle, case_sensitive=False):
        self.calls.append((needle, case_sensitive))


def test_find_marks_follow_the_searched_kinds():
    """The message pane and file list mark only what the find bar searches"""
    message_view = _MarkView()
    file_view = _MarkView()
    dag_widgets.show_find_marks(
        FindState('fix', case_sensitive=True), message_view, file_view
    )
    dag_widgets.show_find_marks(
        FindState('fix', messages=False), message_view, file_view
    )
    dag_widgets.show_find_marks(FindState('fix', files=False), message_view, file_view)
    dag_widgets.show_find_marks(None, message_view, file_view)
    assert message_view.calls == [
        ('fix', True),
        (None, False),
        ('fix', False),
        (None, False),
    ]
    assert file_view.calls == [
        ('fix', True),
        ('fix', False),
        (None, False),
        (None, False),
    ]


def test_set_find_state_announces_the_state(qapp, app_context):
    tree = CommitTreeWidget(app_context, None)
    states = []
    tree.find_state_changed.connect(states.append)
    state = FindState('fix')
    tree.set_find_state(state)
    tree.set_find_state(None)
    assert states == [state, None]


def test_inline_graph_summary_is_elided(qapp, app_context, monkeypatch):
    """A summary too long for the column ends in an ellipsis, not mid-letter"""
    tree = CommitTreeWidget(app_context, None)
    commit = _commit_for_graph(app_context, f'{1:040x}')
    commit.summary = 'a summary far too long to fit the narrow column ' * 4
    tree.add_commits([commit])
    tree.display_inline_graph(True)
    tree.resize(400, 120)
    tree.setColumnWidth(CommitTreeWidgetItem.SUMMARY, 200)

    drawn = []
    draw_text = QtGui.QPainter.drawText

    def record(painter, *args):
        if isinstance(args[-1], str):
            drawn.append(args[-1])
        return draw_text(painter, *args)

    monkeypatch.setattr(QtGui.QPainter, 'drawText', record)
    tree.viewport().grab()
    summaries = [text for text in drawn if text.startswith('a summary')]
    assert summaries
    assert all(text.endswith(chr(0x2026)) for text in summaries)


def test_commit_find_escape_hides(qapp, app_context):
    """Esc hides the find bar and refocuses the widget it was opened from"""
    tree, find, _items = _find_tree(app_context)
    window = QtWidgets.QWidget()
    other = QtWidgets.QLineEdit(window)
    layout = QtWidgets.QVBoxLayout(window)
    layout.addWidget(tree)
    layout.addWidget(find)
    layout.addWidget(other)
    window.show()
    window.activateWindow()
    other.setFocus()
    qapp.processEvents()

    find.show_find()
    assert find.isVisible()
    find.text.esc_pressed.emit()
    qapp.processEvents()
    assert not find.isVisible()
    assert QtWidgets.QApplication.focusWidget() is other
    window.close()


def test_edge_colors_are_rebuilt_not_extended():
    """Opening DAG windows or switching Light/Dark must not grow the list"""
    from cola.widgets.dag import EdgeColor

    EdgeColor.update_colors(dark=False)
    light = [color.name() for color in EdgeColor.colors]
    EdgeColor.update_colors(dark=False)
    assert [color.name() for color in EdgeColor.colors] == light
    EdgeColor.update_colors(dark=True)
    dark = [color.name() for color in EdgeColor.colors]
    assert dark != light
    assert len(dark) < len(light) + 5
    assert QtGui.QColor(Qt.darkBlue).name() not in dark


def test_neighbour_rows_prefetch_ahead_in_the_direction_of_travel():
    from cola.widgets.dag import neighbour_rows

    # Moving down (or first selection): the next three rows, then one above.
    assert neighbour_rows(10, None, 100) == [11, 12, 13, 9]
    assert neighbour_rows(10, 9, 100) == [11, 12, 13, 9]
    # Moving up: the three rows above, then one below.
    assert neighbour_rows(10, 11, 100) == [9, 8, 7, 11]
    # Clipped to the list.
    assert neighbour_rows(0, None, 2) == [1]
    assert neighbour_rows(0, 1, 2) == [1]


def test_graph_cells_skip_rows_where_a_tag_would_overlap():
    """A node moves down past rows whose tagged nodes its label would cover"""
    from cola.widgets.dag import GraphView

    view = GraphView.__new__(GraphView)
    view.min_column = -2
    view.max_column = 1
    view.frontier = {-2: 5, -1: 5, 0: 5, 1: 5}
    # Tagged nodes in row 5 at column 0, and in row 6 at column -2.
    view.tagged_rows = {5: {0}, 6: {-2}}
    # The labels run towards higher columns (x_off < 0), so a node in column
    # -1 overlaps the tag in column 0 but not the one in column -2.
    assert view.x_off < 0
    assert view.alloc_cell(-1, []) == 6
    assert view.frontier[-1] == 7
    # Column 1 has nothing to its right, so row 5 is free.
    assert view.alloc_cell(1, []) == 5
    # A tagged node is pushed past row 5 too, and records its cell.
    assert view.alloc_cell(-2, ['v1.0']) == 6
    assert view.tagged_rows[6] == {-2}


def test_inline_graph_paints_every_lane_in_its_colour(qapp):
    """Pass-through lanes are batched by colour, the merge curve drawn after"""
    from cola.models.graph import build_graph
    from cola.widgets.dag import EdgeColor
    from cola.widgets.dag import GraphDelegate

    # Four side branches stay open across "mid", which merges one of them.
    commits = [('base', [])]
    commits += [(f'side{i}', ['base']) for i in range(4)]
    commits += [('mid', ['base', 'side0']), ('top', ['mid', 'side1', 'side2', 'side3'])]
    rows = {row.commit_oid: row for row in build_graph(commits).rows}
    row = rows['mid']
    prev_row = rows['top']
    assert len(row.edges_to_parent) > 3

    model = QtGui.QStandardItemModel(1, 1)
    item = model.item(0, 0) or QtGui.QStandardItem()
    model.setItem(0, 0, item)
    item.setData(row, GRAPH_ROW_ROLE)
    item.setData(prev_row, GRAPH_PREV_ROW_ROLE)
    delegate = GraphDelegate()
    option = QtWidgets.QStyleOptionViewItem()
    option.rect = QtCore.QRect(0, 0, 200, 24)
    image = QtGui.QImage(200, 24, QtGui.QImage.Format_ARGB32)
    image.fill(Qt.white)
    painter = QtGui.QPainter(image)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()

    lane = GraphDelegate.LANE_WIDTH
    colors = EdgeColor.colors
    checked = 0
    for edge in prev_row.edges_to_parent:
        if edge.to_column == row.commit_column:
            continue  # The commit's dot covers the end of this one.
        x_val = edge.to_column * lane + lane // 2
        expected = QtGui.QColor(colors[edge.color_index % len(colors)])
        actual = image.pixelColor(x_val, 3)
        assert actual.hue() == expected.hue(), (edge, actual.name(), expected.name())
        checked += 1
    for edge in row.edges_to_parent:
        if edge.from_column != edge.to_column or edge.from_column == row.commit_column:
            continue
        x_val = edge.from_column * lane + lane // 2
        expected = QtGui.QColor(colors[edge.color_index % len(colors)])
        assert image.pixelColor(x_val, 21).hue() == expected.hue()
        checked += 1
    assert checked >= 4


class _Node:
    """Enough of a graph Commit node for edges: a position, a commit, edges"""

    def __init__(self, commit, x_val, y_val):
        self.commit = commit
        self.edges = {}
        self.point = QtCore.QPointF(x_val, y_val)

    def pos(self):
        return self.point

    def x(self):
        return self.point.x()


def _graph_with_nodes(app_context, coordinates):
    """A GraphView holding fake nodes for linear commits at the coordinates"""
    from cola.widgets.dag import GraphView

    view = GraphView.__new__(GraphView)
    QtWidgets.QGraphicsView.__init__(view)
    view.setScene(QtWidgets.QGraphicsScene(view))
    commits = []
    parent = None
    for idx in range(len(coordinates)):
        parent = _commit_for_graph(
            app_context, f'{idx:040x}', parents=[parent] if parent else None
        )
        commits.append(parent)
    view.items = {
        commit.oid: _Node(commit, *xy) for commit, xy in zip(commits, coordinates)
    }
    view.positions = {commit.oid: xy for commit, xy in zip(commits, coordinates)}
    return view, commits


def test_graph_edges_are_drawn_by_a_few_group_items(qapp, app_context, monkeypatch):
    """67k edges as separate scene items took 0.48 s to create for 50000 commits"""
    from cola.widgets import dag as dag_widgets

    monkeypatch.setattr(dag_widgets, 'EDGES_PER_GROUP', 4)
    coordinates = [(0.0, -50.0 * idx) for idx in range(10)]
    view, commits = _graph_with_nodes(app_context, coordinates)
    view.link(commits)

    groups = view.scene().items()
    assert len(groups) == 3  # nine edges in groups of four
    assert all(isinstance(group, dag_widgets.EdgeGroup) for group in groups)
    first, second = view.items[commits[0].oid], view.items[commits[1].oid]
    assert first.edges[commits[1].oid] is second.edges[commits[0].oid]
    # Linking again adds nothing.
    view.link(commits)
    assert len(view.scene().items()) == 3
    # A group spans its edges, but is never hit by itemAt().
    for group in groups:
        rect = group.boundingRect()
        for edge in group.edges:
            assert rect.contains(QtCore.QPointF(edge.left, edge.top))
            assert rect.contains(QtCore.QPointF(edge.right, edge.bottom))
        assert group.shape().isEmpty()
    assert view.scene().itemAt(QtCore.QPointF(0, -25), QtGui.QTransform()) is None


def test_graph_edge_group_follows_a_moved_commit(qapp, app_context):
    view, commits = _graph_with_nodes(app_context, [(0.0, 0.0), (0.0, -50.0)])
    view.link(commits)
    (group,) = view.scene().items()
    (edge,) = group.edges
    assert group.boundingRect().right() < 100
    view.items[commits[1].oid].point = QtCore.QPointF(300.0, -50.0)
    edge.commits_were_invalidated()
    assert (edge.left, edge.right) == (0.0, 300.0)
    assert group.boundingRect().right() >= 300


def test_graph_edge_group_paints_only_the_exposed_edges(qapp, app_context):
    coordinates = [(0.0, -100.0 * idx) for idx in range(6)]
    view, commits = _graph_with_nodes(app_context, coordinates)
    view.link(commits)
    (group,) = view.scene().items()
    painter = MagicMock()
    option = QtWidgets.QStyleOptionGraphicsItem()
    option.exposedRect = QtCore.QRectF(-10, -260, 20, 20)  # around y=-250

    group.paint(painter, option, None)
    # Only the edges between y=-200 and y=-300 cross it.
    assert painter.drawPath.call_count == 1


def test_graph_edge_group_recolours_each_shared_pen_once(qapp, app_context):
    from cola.widgets.dag import EdgeColor

    EdgeColor.update_colors(dark=False)
    view, commits = _graph_with_nodes(app_context, [(0.0, -10.0 * i) for i in range(5)])
    view.link(commits)
    (group,) = view.scene().items()
    pens = {id(edge.pen) for edge in group.edges}
    assert len(pens) == 1
    EdgeColor.update_colors(dark=True)
    group.refresh_colors()
    edge = group.edges[0]
    expected = EdgeColor.colors[edge.color_index % len(EdgeColor.colors)]
    assert edge.pen.color().rgb() == expected.rgb()


def _gesture_graph():
    """A GraphView with a scene, for the wheel and gesture tests"""
    from cola.widgets.dag import GraphView

    view = GraphView.__new__(GraphView)
    QtWidgets.QGraphicsView.__init__(view)
    scene = QtWidgets.QGraphicsScene(view)
    scene.setSceneRect(-5000, -5000, 10000, 10000)
    view.setScene(scene)
    view.resize(400, 300)
    view.zoom = 1.0
    return view


def _wheel(pixel, angle, modifiers=Qt.NoModifier, phase=Qt.NoScrollPhase):
    return QtGui.QWheelEvent(
        QtCore.QPointF(200, 150),
        QtCore.QPointF(200, 150),
        QtCore.QPoint(*pixel),
        QtCore.QPoint(*angle),
        Qt.NoButton,
        modifiers,
        phase,
        False,
    )


def test_graph_trackpad_scroll_pans_by_the_finger_distance(qapp):
    """macOS reports angleDelta as twice pixelDelta for a trackpad"""
    view = _gesture_graph()
    view.wheelEvent(_wheel((0, 30), (0, 60)))
    assert view.transform().dy() == 30
    # A mouse wheel has no pixel delta and still pans by the angle.
    view.wheelEvent(_wheel((0, 0), (0, 120)))
    assert view.transform().dy() == 150


def test_graph_ctrl_scroll_zooms_but_momentum_does_not(qapp):
    view = _gesture_graph()
    view.wheelEvent(_wheel((0, 30), (0, 60), Qt.ControlModifier, Qt.ScrollMomentum))
    assert view.transform().m11() == 1.0
    view.wheelEvent(_wheel((0, 30), (0, 256), Qt.ControlModifier, Qt.ScrollUpdate))
    assert view.transform().m11() == pytest.approx(2**0.5)


def test_graph_pinch_zooms_around_the_fingers(qapp):
    view = _gesture_graph()
    point = QtCore.QPointF(120, 80)
    before = view.mapToScene(point.toPoint())
    event = QtGui.QNativeGestureEvent(
        Qt.ZoomNativeGesture,
        QtGui.QPointingDevice.primaryPointingDevice(),
        2,
        point,
        point,
        point,
        0.5,
        QtCore.QPointF(),
    )
    assert view.viewportEvent(event)
    assert view.transform().m11() == pytest.approx(1.5)
    after = view.mapToScene(point.toPoint())
    assert abs(after.x() - before.x()) <= 1
    assert abs(after.y() - before.y()) <= 1


def test_graph_layout_leaves_unmoved_nodes_and_their_edges_alone():
    from cola.widgets.dag import GraphView

    view = GraphView.__new__(GraphView)
    still, moved = MagicMock(), MagicMock()
    still.pos.return_value = QtCore.QPointF(10.0, 20.0)
    moved.pos.return_value = QtCore.QPointF(0.0, 0.0)
    still_edge, moved_edge = MagicMock(), MagicMock()
    still.edges = {'a': still_edge}
    moved.edges = {'b': moved_edge}
    view.items = {'still': still, 'moved': moved}
    view.position_nodes = lambda: {'still': (10.0, 20.0), 'moved': (30.0, 40.0)}
    view.layout_commits()
    still.setPos.assert_not_called()
    still_edge.commits_were_invalidated.assert_not_called()
    moved.setPos.assert_called_once_with(30.0, 40.0)
    moved_edge.commits_were_invalidated.assert_called_once_with()


def _commit_list(app_context, count=5):
    """A commit list with a linear history, newest first"""
    tree = CommitTreeWidget(app_context, None)
    commits = []
    parent = None
    for idx in range(count):
        parents = [parent] if parent else None
        parent = _commit_for_graph(app_context, f'{idx:x}' * 40, parents=parents)
        commits.append(parent)
    tree.add_commits(commits)
    return tree


@pytest.mark.parametrize('keypad', [False, True])
def test_cmd_up_and_down_select_the_first_and_last_commit_on_macos(
    qapp, app_context, monkeypatch, keypad
):
    """macOS arrow keys can carry KeypadModifier, which must not matter"""
    from qtpy.QtTest import QTest

    monkeypatch.setattr('cola.widgets.dag.core.IS_DARWIN', True)
    tree = _commit_list(app_context)
    middle = tree.topLevelItem(2)
    tree.select([middle.commit.oid])
    mods = Qt.ControlModifier | (Qt.KeypadModifier if keypad else Qt.NoModifier)

    QTest.keyClick(tree, Qt.Key_Up, mods)
    assert tree.selectedItems() == [tree.topLevelItem(0)]
    assert tree.currentItem() is tree.topLevelItem(0)

    QTest.keyClick(tree, Qt.Key_Down, mods)
    last = tree.topLevelItem(tree.topLevelItemCount() - 1)
    assert tree.selectedItems() == [last]
    assert tree.currentItem() is last


def test_ctrl_up_keeps_qt_behaviour_off_macos(qapp, app_context, monkeypatch):
    """Elsewhere Ctrl+Up still moves the focus without changing the selection"""
    from qtpy.QtTest import QTest

    monkeypatch.setattr('cola.widgets.dag.core.IS_DARWIN', False)
    tree = _commit_list(app_context)
    middle = tree.topLevelItem(2)
    tree.select([middle.commit.oid])
    QTest.keyClick(tree, Qt.Key_Up, Qt.ControlModifier)
    assert tree.selectedItems() == [middle]
