"""Tests DAG functionality"""
import sys
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from cola.models import dag
from cola.widgets.dag import COMMIT_ROLE
from cola.widgets.dag import GRAPH_PREV_ROW_ROLE
from cola.widgets.dag import GRAPH_ROW_ROLE
from cola.widgets.dag import Commit as GraphicsCommit
from cola.widgets.dag import CommitTreeWidget
from cola.widgets.dag import CommitTreeWidgetItem
from cola.widgets.dag import GitDAG
from cola.widgets.dag import _prepare_labels
from cola.widgets.dag import _prepare_labels_cached
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

SIGNER = 'David Aguilar <davvid@gmail.com>'
SIGNING_KEY = 'ABCDEF0123456789'


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
    _mock_core_git_log(core, LOG_TEXT)
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
    _mock_core_git_log(core, LOG_TEXT)
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
    _mock_core_git_log(core, LOG_TEXT)
    for idx, commit in enumerate(dag_context.reader.get()):
        assert timestamps[idx] == commit.timestamp
        assert commit.authdate.endswith('2007 -0800')


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


@patch('cola.models.dag.core')
def test_repo_reader_never_verifies_signatures(core, dag_context):
    """Signature verification is kept out of the log that populates the DAG

    The %G? placeholders make "git log" verify every signed commit before it
    emits anything, which would hold up the whole graph.
    """
    commit_files()
    dag_context.context.model.update_status()
    _mock_core_git_log(core, LOG_TEXT)
    commits = list(dag_context.reader.get())

    assert commits
    assert '%G?' not in ' '.join(dag_context.reader._cmd)
    for commit in commits:
        assert commit.signature == ''
        assert dag.signature_severity(commit) == dag.SignatureStatus.NONE
        assert dag.signature_tooltip(commit) == ''


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


@patch('cola.models.dag.core')
def test_read_signatures(core, app_context):
    """Signatures are read separately, keyed by oid"""
    oids = ['a' * 40, 'b' * 40]
    sep = chr(0x01)
    core.run_command.return_value = (
        0,
        f'{oids[0]}{sep}G{sep}{SIGNER}{sep}{SIGNING_KEY}\n'
        f'{oids[1]}{sep}B{sep}{sep}',
        '',
    )

    signatures = dag.read_signatures(app_context, oids)

    assert signatures[oids[0]] == ('G', SIGNER, SIGNING_KEY)
    assert signatures[oids[1]] == ('B', '', '')

    cmd = core.run_command.call_args[0][0]
    assert '%G?' in ' '.join(cmd)
    # --no-walk keeps the output to the commits asked for.
    assert '--no-walk=unsorted' in cmd
    assert cmd[-2:] == oids


@patch('cola.models.dag.core')
def test_read_signatures_drops_pseudo_commits(core, app_context):
    """STAGE and WORKTREE must never reach "git log"

    They are not revisions, and a single unknown argument makes the command
    fail, which would lose the signatures of every real commit in the batch.
    """
    oid = 'a' * 40
    sep = chr(0x01)
    core.run_command.return_value = (
        0,
        f'{oid}{sep}G{sep}{SIGNER}{sep}{SIGNING_KEY}',
        '',
    )

    signatures = dag.read_signatures(app_context, [dag.STAGE, oid, dag.WORKTREE])

    cmd = core.run_command.call_args[0][0]
    assert dag.STAGE not in cmd
    assert dag.WORKTREE not in cmd
    assert signatures == {oid: ('G', SIGNER, SIGNING_KEY)}


def test_read_signatures_with_only_pseudo_commits(app_context):
    """A batch of nothing but placeholders runs no command at all"""
    with patch('cola.models.dag.core') as core:
        assert dag.read_signatures(app_context, [dag.STAGE, dag.WORKTREE]) == {}
    core.run_command.assert_not_called()


def test_read_signatures_task_skips_work_when_stopping(app_context):
    """A batch queued when the window closes is abandoned, not run

    The application waits for the thread pool to drain before exiting, so
    running it anyway would hold up quitting.
    """
    from cola.widgets.dag import read_signatures_task

    oids = ['a' * 40]
    with patch('cola.models.dag.core') as core:
        result = read_signatures_task(app_context, oids, lambda: True)

    assert result == (oids, {})
    core.run_command.assert_not_called()


def test_read_signatures_without_oids(app_context):
    """No commits means no subprocess at all"""
    with patch('cola.models.dag.core') as core:
        assert dag.read_signatures(app_context, []) == {}
    core.run_command.assert_not_called()


@patch('cola.models.dag.core')
def test_read_signatures_when_git_fails(core, app_context):
    """A failed verification pass yields nothing rather than raising"""
    core.run_command.return_value = (128, '', 'fatal: bad object')
    assert dag.read_signatures(app_context, ['a' * 40]) == {}


@patch('cola.models.dag.core')
def test_read_signatures_ignores_malformed_lines(core, app_context):
    """Unexpected output is skipped instead of breaking the batch"""
    oid = 'a' * 40
    sep = chr(0x01)
    core.run_command.return_value = (
        0,
        f'garbage\n{oid}{sep}G{sep}{SIGNER}{sep}{SIGNING_KEY}\n\n',
        '',
    )

    signatures = dag.read_signatures(app_context, [oid])

    assert signatures == {oid: ('G', SIGNER, SIGNING_KEY)}


@pytest.mark.parametrize(
    ('status', 'severity'),
    [
        ('G', dag.SignatureStatus.GOOD),
        ('U', dag.SignatureStatus.UNKNOWN),
        ('E', dag.SignatureStatus.UNKNOWN),
        ('B', dag.SignatureStatus.BAD),
        ('R', dag.SignatureStatus.BAD),
        ('N', dag.SignatureStatus.NONE),
        ('', dag.SignatureStatus.NONE),
    ],
)
def test_signature_severity_mapping(app_context, status, severity):
    """Every status character git can emit maps onto a severity"""
    commit = dag.Commit(app_context, oid='a' * 40)
    commit.signature = status
    assert dag.signature_severity(commit) == severity


def test_signature_tooltip_reports_both_verdicts(app_context):
    """Local and GitHub verdicts are both shown, even when they disagree"""
    commit = dag.Commit(app_context, oid='a' * 40)
    commit.signature = 'U'
    commit.signer = 'Josh Taylor <josh@example.com>'
    commit.signing_key = 'CCDD0A75B2820EA9'
    commit.github_verification = {'verified': True, 'reason': 'valid'}

    tooltip = dag.signature_tooltip(commit)

    assert 'Good signature with unknown validity' in tooltip
    assert 'Josh Taylor <josh@example.com>' in tooltip
    assert 'CCDD0A75B2820EA9' in tooltip
    assert 'Verified by GitHub' in tooltip


def test_signature_tooltip_unverified_by_github(app_context):
    """GitHub's reason is reported when it refuses to verify a signature"""
    commit = dag.Commit(app_context, oid='a' * 40)
    commit.signature = 'G'
    commit.github_verification = {'verified': False, 'reason': 'unknown_key'}

    tooltip = dag.signature_tooltip(commit)

    assert 'Good signature' in tooltip
    assert 'unknown_key' in tooltip


def test_signature_label(app_context):
    """Each signature status character maps onto a label"""
    commit = dag.Commit(app_context, oid='a' * 40)
    assert dag.signature_label(commit) == ''

    commit.signature = 'G'
    assert dag.signature_label(commit) == 'Verified'

    commit.signature = 'N'
    assert dag.signature_label(commit) == ''


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
    with patch.object(dag, 'signature_tooltip', return_value='') as signature_mock:
        item = GraphicsCommit(commit)
        assert item.toolTip() == ''
        signature_mock.assert_not_called()

        item.ensure_tooltip()
        assert item.toolTip() == commit.oid[:12] + ': ' + commit.summary
        item.ensure_tooltip()
        # Idempotent: the second call reused the built tooltip.
        assert signature_mock.call_count == 1


def test_graph_node_tooltip_refreshes_only_after_materialisation(qapp, app_context):
    """Signature updates rebuild only tooltips that were already built"""
    commit = _commit_for_graph(app_context, 'a' * 40)
    signatures = ['', 'Signature: Good']
    with patch.object(dag, 'signature_tooltip', side_effect=signatures):
        item = GraphicsCommit(commit)
        # A signature update before any hover leaves the tooltip unbuilt; the
        # first hover reads the current signature state anyway.
        item.update_tooltip()
        assert item.toolTip() == ''

        item.ensure_tooltip()
        base = commit.oid[:12] + ': ' + commit.summary
        assert item.toolTip() == base
        # Once built, a signature update refreshes the text immediately.
        item.update_tooltip()
        assert item.toolTip() == base + '\nSignature: Good'


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
    # thread_end drives these; they are exercised by their own tests.
    win.restore_selection = MagicMock()
    win.start_signature_verification = MagicMock()
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


def test_superseded_deferred_build_bails(qapp, app_context):
    """A deferred build from a prior reload no-ops once a new reload starts."""
    win = _make_lazy_graph_dag(app_context, visible=True)
    win._commits_loaded = True

    # A newer reload has bumped the build id since this build was queued.
    win._graph_build_id = 2
    win._populate_graphview(1)

    win.graphview.add_commits.assert_not_called()
    assert win._graph_stale is True
